"""龙虎榜告警投递状态机（T11）。SENT 无 ACK 不得显示为已送达。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.data_point import canonical_json, content_hash_for
from ab_screener.operations.alerts import raise_alert

_TZ = ZoneInfo("Asia/Shanghai")
CREATED = "CREATED"
SENT = "SENT"
ACKED = "ACKED"
FAILED = "FAILED"
DEAD_LETTER = "DEAD_LETTER"
MAX_ATTEMPTS = 3
ALERT_TYPES = (
    "LARGE_NET_BUY",
    "INDEPENDENT_RESONANCE",
    "REPEAT_APPEARANCE",
    "BUY_TO_SELL",
    "MAPPING_DRIFT",
    "DATA_QUALITY",
)


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def delivery_label(status: str) -> str:
    if status == ACKED:
        return "已送达"
    if status == SENT:
        return "已发送未确认"
    if status == FAILED:
        return "发送失败"
    if status == DEAD_LETTER:
        return "死信"
    return "已创建未发送"


def _latest(conn: sqlite3.Connection, alert_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT delivery_id, revision, status, attempt, channel, dry_run, last_error"
        " FROM lhb_alert_delivery WHERE alert_id=? ORDER BY revision DESC LIMIT 1",
        (alert_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "delivery_id": row[0],
        "revision": int(row[1]),
        "status": row[2],
        "attempt": int(row[3]),
        "channel": row[4],
        "dry_run": bool(row[5]),
        "last_error": row[6],
        "delivered": row[2] == ACKED,
        "label": delivery_label(row[2]),
    }


def _append(
    conn: sqlite3.Connection,
    *,
    alert_id: str,
    status: str,
    attempt: int,
    channel: str,
    dry_run: bool,
    last_error: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prev = _latest(conn, alert_id)
    revision = 1 if prev is None else prev["revision"] + 1
    delivery_id = prev["delivery_id"] if prev else f"del-{alert_id}"
    body = {
        "alert_id": alert_id,
        "status": status,
        "attempt": attempt,
        "channel": channel,
        "dry_run": dry_run,
        "last_error": last_error,
        "payload": payload or {},
    }
    digest = content_hash_for(body)
    now = _now()
    conn.execute(
        "INSERT INTO lhb_alert_delivery (delivery_id, alert_id, revision, status, attempt,"
        " channel, dry_run, last_error, source, available_at, ingested_at, content_hash,"
        " payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            delivery_id,
            alert_id,
            revision,
            status,
            attempt,
            channel,
            1 if dry_run else 0,
            last_error,
            "lhb-alerts",
            now,
            now,
            digest,
            canonical_json(body),
        ),
    )
    conn.commit()
    return {
        "delivery_id": delivery_id,
        "alert_id": alert_id,
        "revision": revision,
        "status": status,
        "attempt": attempt,
        "channel": channel,
        "dry_run": dry_run,
        "delivered": status == ACKED,
        "label": delivery_label(status),
        "last_error": last_error,
    }


def create_alert(
    conn: sqlite3.Connection,
    *,
    alert_type: str,
    trade_date: str,
    payload: dict[str, Any],
    severity: str = "INFO",
    channel: str = "TEST",
    dry_run: bool = True,
) -> dict[str, Any]:
    if alert_type not in ALERT_TYPES:
        raise ValueError(f"未知告警类型: {alert_type}")
    alert_id = raise_alert(
        conn,
        alert_type=alert_type,
        source="lhb",
        trade_date=trade_date,
        severity=severity,
        payload=payload,
    )
    existing = _latest(conn, alert_id)
    if existing is not None:
        return {"alert_id": alert_id, "deduped": True, **existing}
    rec = _append(
        conn,
        alert_id=alert_id,
        status=CREATED,
        attempt=0,
        channel=channel,
        dry_run=dry_run,
        payload=payload,
    )
    rec["alert_id"] = alert_id
    rec["deduped"] = False
    return rec


def dispatch(
    conn: sqlite3.Connection,
    alert_id: str,
    *,
    notify_fn: Any | None = None,
    dry_run: bool = True,
    historical_replay: bool = False,
) -> dict[str, Any]:
    """历史重放默认不发真实通知。调用成功但无 ACK 仍不是已送达。"""
    cur = _latest(conn, alert_id)
    if cur is None:
        raise ValueError(f"告警不存在: {alert_id}")
    if cur["status"] == ACKED:
        return cur
    if historical_replay or dry_run:
        return _append(
            conn,
            alert_id=alert_id,
            status=CREATED,
            attempt=cur["attempt"],
            channel="TEST",
            dry_run=True,
            last_error="HISTORICAL_REPLAY_NO_NOTIFY" if historical_replay else "DRY_RUN",
        )
    attempt = cur["attempt"] + 1
    try:
        if notify_fn is None:
            raise RuntimeError("无通知通道")
        notify_fn(alert_id)
    except Exception as exc:  # noqa: BLE001
        status = DEAD_LETTER if attempt >= MAX_ATTEMPTS else FAILED
        return _append(
            conn,
            alert_id=alert_id,
            status=status,
            attempt=attempt,
            channel=cur["channel"],
            dry_run=False,
            last_error=str(exc),
        )
    return _append(
        conn,
        alert_id=alert_id,
        status=SENT,
        attempt=attempt,
        channel=cur["channel"],
        dry_run=False,
    )


def ack(conn: sqlite3.Connection, alert_id: str) -> dict[str, Any]:
    cur = _latest(conn, alert_id)
    if cur is None:
        raise ValueError(f"告警不存在: {alert_id}")
    if cur["status"] != SENT:
        return cur
    return _append(
        conn,
        alert_id=alert_id,
        status=ACKED,
        attempt=cur["attempt"],
        channel=cur["channel"],
        dry_run=cur["dry_run"],
    )


def retry_failed(conn: sqlite3.Connection, alert_id: str, *, notify_fn: Any) -> dict[str, Any]:
    cur = _latest(conn, alert_id)
    if cur is None:
        raise ValueError(f"告警不存在: {alert_id}")
    if cur["status"] not in (FAILED, SENT):
        return cur
    return dispatch(conn, alert_id, notify_fn=notify_fn, dry_run=False, historical_replay=False)
