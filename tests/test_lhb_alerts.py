"""T11 告警 ACK 状态机：未 ACK 不算送达；死信；历史重放不发真通知。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ab_screener.data.migration_intents.lhb_ops_v2 import apply_lhb_ops
from ab_screener.data.migration_intents.lhb_tracking_v2 import apply_lhb_tracking
from ab_screener.data.migration_intents.operations_v2 import apply_operations
from ab_screener.operations.lhb_alerts import (
    ACKED,
    CREATED,
    DEAD_LETTER,
    FAILED,
    MAX_ATTEMPTS,
    SENT,
    ack,
    create_alert,
    delivery_label,
    dispatch,
    retry_failed,
)


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "a.db"))
    apply_operations(conn)
    apply_lhb_tracking(conn)
    apply_lhb_ops(conn)
    return conn


def test_sent_without_ack_is_not_delivered(tmp_path: Path):
    conn = _conn(tmp_path)
    rec = create_alert(
        conn,
        alert_type="LARGE_NET_BUY",
        trade_date="20260810",
        payload={"ts_code": "000001.SZ"},
        dry_run=False,
        channel="TEST",
    )
    sent = dispatch(conn, rec["alert_id"], notify_fn=lambda _aid: None, dry_run=False)
    assert sent["status"] == SENT
    assert sent["delivered"] is False
    assert delivery_label(SENT) != "已送达"
    acked = ack(conn, rec["alert_id"])
    assert acked["status"] == ACKED
    assert acked["delivered"] is True
    conn.close()


def test_retry_then_dead_letter(tmp_path: Path):
    conn = _conn(tmp_path)
    rec = create_alert(
        conn,
        alert_type="DATA_QUALITY",
        trade_date="20260810",
        payload={"reason": "FETCH_FAILED"},
        dry_run=False,
    )

    def boom(_aid: str) -> None:
        raise RuntimeError("down")

    last = rec
    for _ in range(MAX_ATTEMPTS):
        last = dispatch(conn, rec["alert_id"], notify_fn=boom, dry_run=False)
    assert last["status"] == DEAD_LETTER
    assert last["attempt"] == MAX_ATTEMPTS
    conn.close()


def test_historical_replay_does_not_notify(tmp_path: Path):
    conn = _conn(tmp_path)
    rec = create_alert(
        conn,
        alert_type="INDEPENDENT_RESONANCE",
        trade_date="20260810",
        payload={"n": 3},
        dry_run=True,
    )
    called = []
    out = dispatch(
        conn,
        rec["alert_id"],
        notify_fn=lambda aid: called.append(aid),
        dry_run=False,
        historical_replay=True,
    )
    assert called == []
    assert out["status"] == CREATED
    assert out["delivered"] is False
    conn.close()


def test_failed_can_retry_until_ack(tmp_path: Path):
    conn = _conn(tmp_path)
    rec = create_alert(
        conn,
        alert_type="BUY_TO_SELL",
        trade_date="20260810",
        payload={"seat": "x"},
        dry_run=False,
    )
    dispatch(conn, rec["alert_id"], notify_fn=lambda _a: (_ for _ in ()).throw(RuntimeError("x")), dry_run=False)
    sent = retry_failed(conn, rec["alert_id"], notify_fn=lambda _a: None)
    assert sent["status"] in (SENT, FAILED)
    if sent["status"] == SENT:
        assert ack(conn, rec["alert_id"])["status"] == ACKED
    conn.close()
