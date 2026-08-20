"""P6.2 告警/审计测试：告警幂等、审计 append-only、hash chain、脱敏、签名锚定。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.application.audit_service import (
    record_audit_event,
    sign_chain_head,
    verify_audit_chain,
    verify_chain_head,
)
from ab_screener.data.migration_registry import apply_pending
from ab_screener.operations.alerts import raise_alert


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "audit.db"))
    apply_pending(c)
    yield c
    c.close()


def test_alert_idempotent(conn):
    a1 = raise_alert(conn, alert_type="STALE_DATA", source="dag",
                     trade_date="20260810", severity="HIGH", payload={"days": 3})
    a2 = raise_alert(conn, alert_type="STALE_DATA", source="dag",
                     trade_date="20260810", severity="HIGH", payload={"days": 3})
    assert a1 == a2
    n = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
    assert n == 1


def test_audit_append_only(conn):
    """审计只追加：行只增不删；篡改由 hash chain 检测（见 tamper 测试）。"""
    record_audit_event(conn, actor="user", action="ORDER_CONFIRM",
                       request={"order_id": "O1"}, correlation_id="C1")
    n = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert n == 1
    # 再记录一条 → 链增长（追加不覆盖）
    record_audit_event(conn, actor="system", action="SETTLE",
                       request={"date": "20260810"}, correlation_id="C2")
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2
    # 删除链中间行 → 断链被检测
    conn.execute("DELETE FROM audit_events WHERE action='ORDER_CONFIRM'")
    assert verify_audit_chain(conn)["valid"] is False


def test_audit_hash_chain_valid_and_tamper_detected(conn):
    record_audit_event(conn, actor="a", action="X1", request={"k": 1}, correlation_id="C1")
    record_audit_event(conn, actor="a", action="X2", request={"k": 2}, correlation_id="C2")
    assert verify_audit_chain(conn)["valid"] is True
    # 篡改 before_json → 断链
    conn.execute("UPDATE audit_events SET before_json='{\"evil\":1}' WHERE action='X2'")
    check = verify_audit_chain(conn)
    assert check["valid"] is False


def test_audit_sanitizes_sensitive_fields(conn):
    record_audit_event(conn, actor="a", action="SYNC",
                       request={"ts_token": "SECRET123"}, correlation_id="C1")
    row = conn.execute("SELECT request_json FROM audit_events").fetchone()[0]
    assert "SECRET123" not in row
    assert "[REDACTED]" in row


def test_audit_sign_anchor_and_verify(conn, tmp_path: Path):
    record_audit_event(conn, actor="a", action="X", request={"k": 1}, correlation_id="C1")
    anchor_dir = tmp_path / "anchors"
    anchor = sign_chain_head(conn, anchor_dir)
    assert verify_chain_head(conn, anchor) is True
    # 篡改后验证失败
    conn.execute("UPDATE audit_events SET action='EVIL'")
    assert verify_chain_head(conn, anchor) is False
