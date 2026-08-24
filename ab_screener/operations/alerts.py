"""事件化告警（V2R-O2）：幂等去重、递归脱敏、GET/只读查询零写入。

修复项：
- payload/error 与审计同款递归脱敏：Token、密码、API key 或完整账户号
  禁止进入持久记录（alert_events）。
- dedupe_key 基于脱敏后 payload 计算，重复事件幂等返回既有。
- 只读查询（alert_exists / list_alerts_at）用 readonly 连接，零写入。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

_SENSITIVE_TAGS = ("token", "password", "secret", "apikey", "api_key", "key")
_SENSITIVE_PATTERNS = (
    "account_number", "account_no", "bank_account", "card_number", "full_account",
)


class AlertError(RuntimeError):
    """告警错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _sanitize(value: Any) -> Any:
    """与审计同款递归脱敏（Token/密码/API key/完整账户号 → [REDACTED]）。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            low = str(k).lower()
            if any(tag in low for tag in _SENSITIVE_TAGS) or any(pattern in low for pattern in _SENSITIVE_PATTERNS):
                out[k] = "[REDACTED]"
            else:
                out[k] = _sanitize(v)
        return out
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str) and len(value) == 18 and value.isdigit():
        return "[REDACTED]"
    return value


def raise_alert(
    conn: sqlite3.Connection,
    *,
    alert_type: str,
    source: str,
    trade_date: str,
    severity: str,
    payload: dict[str, Any],
) -> str:
    """事件化告警；dedupe_key 相同 → 幂等返回既有；payload 脱敏后落盘。"""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alert_events'"
    ).fetchone()
    if not has:
        raise AlertError("alert_events 表不存在：先运行 migrate_v2.py --apply（fail-closed）")
    payload_san = _sanitize(payload)
    dedupe = hashlib.sha256(
        json.dumps({"type": alert_type, "source": source, "date": trade_date,
                    "payload": payload_san}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    existing = conn.execute(
        "SELECT alert_id FROM alert_events WHERE dedupe_key=?", (dedupe,)
    ).fetchone()
    if existing:
        return existing[0]
    alert_id = hashlib.sha256(f"{dedupe}|{_now()}".encode()).hexdigest()[:16]
    conn.execute(
        "INSERT INTO alert_events (alert_id, alert_type, source, trade_date, severity,"
        " payload_json, dedupe_key, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (alert_id, alert_type, source, trade_date, severity,
         json.dumps(payload_san, ensure_ascii=False, sort_keys=True, default=str), dedupe, _now()),
    )
    conn.commit()
    return alert_id


def list_alerts_at(
    db_path: str | Path,
    trade_date: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    from ab_screener.data.db import SchemaMissing, connect, table_exists

    with connect(Path(db_path), readonly=True) as conn:
        if not table_exists(conn, "alert_events"):
            raise SchemaMissing("告警表未迁移")
        return list_alerts(conn, trade_date=trade_date, limit=limit)


def alert_exists(db_path: str | Path, alert_id: str) -> bool:
    from ab_screener.data.db import SchemaMissing, connect, table_exists

    with connect(Path(db_path), readonly=True) as conn:
        if not table_exists(conn, "alert_events"):
            raise SchemaMissing("告警表未迁移")
        row = conn.execute(
            "SELECT alert_id FROM alert_events WHERE alert_id=?", (alert_id,)
        ).fetchone()
        return row is not None


def list_alerts(
    conn: sqlite3.Connection, trade_date: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT alert_id, alert_type, source, trade_date, severity, payload_json, created_at"
        " FROM alert_events" + (" WHERE trade_date=?" if trade_date else "")
        + " ORDER BY created_at DESC LIMIT ?",
        ([trade_date] if trade_date else []) + [limit],
    ).fetchall()
    return [
        {"alert_id": r[0], "alert_type": r[1], "source": r[2], "trade_date": r[3],
         "severity": r[4], "payload": json.loads(r[5]), "created_at": r[6]}
        for r in rows
    ]
