"""P6.2 告警/审计测试：告警幂等+脱敏、审计幂等、append-only、hash chain、防分叉、签名注入。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from ab_screener.application.audit_service import (
    AuditError,
    record_audit_event,
    sign_chain_head,
    verify_audit_chain,
    verify_chain_head,
)
from ab_screener.data.migration_registry import apply_pending
from ab_screener.operations.alerts import raise_alert

TEST_KEY = b"v2r-o2-test-signing-key-0001"


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


def test_alert_sanitizes_sensitive_fields(conn):
    """告警 payload 与审计同款递归脱敏：Token/密码/API key/完整账户号不落盘。"""
    raise_alert(conn, alert_type="SYNC", source="dag", trade_date="20260810",
                severity="HIGH",
                payload={"ts_token": "SECRET123", "password": "pw",
                         "api_key": "ak-999", "account_number": "622202020011223344",
                         "nested": {"access_token": "tok", "ok": 1}})
    row = conn.execute("SELECT payload_json FROM alert_events").fetchone()[0]
    assert "SECRET123" not in row
    assert "622202020011223344" not in row
    assert row.count("[REDACTED]") >= 4
    # 读取走只读查询，payload 已脱敏
    from ab_screener.operations.alerts import list_alerts

    alerts = list_alerts(conn)
    payload = alerts[0]["payload"]
    assert payload["ts_token"] == "[REDACTED]"
    assert payload["account_number"] == "[REDACTED]"
    assert payload["nested"]["access_token"] == "[REDACTED]"
    assert payload["nested"]["ok"] == 1


def test_alert_readonly_queries_no_write(tmp_path):
    """GET/只读查询（alert_exists/list_alerts_at）零写入。"""
    from ab_screener.operations.alerts import alert_exists, list_alerts_at

    db = str(tmp_path / "ro.db")
    with sqlite3.connect(db) as c:
        apply_pending(c)
        aid = raise_alert(c, alert_type="X", source="s", trade_date="20260810",
                          severity="LOW", payload={"ok": 1})
    before = (tmp_path / "ro.db").stat().st_size
    assert alert_exists(db, aid) is True
    assert len(list_alerts_at(db, trade_date="20260810")) == 1
    assert (tmp_path / "ro.db").stat().st_size == before


def test_audit_append_only(conn):
    """审计只追加：行只增不删；篡改由 hash chain 检测。"""
    record_audit_event(conn, actor="user", action="ORDER_CONFIRM",
                       request={"order_id": "O1"}, correlation_id="C1")
    n = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert n == 1
    record_audit_event(conn, actor="system", action="SETTLE",
                       request={"date": "20260810"}, correlation_id="C2")
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2
    conn.execute("DELETE FROM audit_events WHERE action='ORDER_CONFIRM'")
    assert verify_audit_chain(conn)["valid"] is False


def test_audit_same_request_idempotent(conn):
    """同 correlation+action+request 重放 → 幂等返回既有事件，不重复写。"""
    e1 = record_audit_event(conn, actor="system", action="DAG_RUN_FINISHED",
                            request={"trade_date": "20260810", "status": "COMPLETED"},
                            correlation_id="R1")
    e2 = record_audit_event(conn, actor="system", action="DAG_RUN_FINISHED",
                            request={"trade_date": "20260810", "status": "COMPLETED"},
                            correlation_id="R1")
    assert e1 == e2
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
    # 不同请求 → 新事件
    e3 = record_audit_event(conn, actor="system", action="DAG_RUN_FINISHED",
                            request={"trade_date": "20260811", "status": "COMPLETED"},
                            correlation_id="R1")
    assert e3 != e1
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2


def test_audit_concurrent_no_fork(conn, tmp_path):
    """并发写同内容事件 → 不产生分叉链（同一事件只写一行）。"""
    db = str(tmp_path / "audit.db")  # 与 conn fixture 同一文件
    barrier = threading.Barrier(2)
    errors: list[Exception] = []
    ids: list[str] = []

    def writer():
        c2 = sqlite3.connect(db)
        try:
            barrier.wait(timeout=10)
            eid = record_audit_event(c2, actor="system", action="SYNC",
                                     request={"day": "20260810"}, correlation_id="C9")
            ids.append(eid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            c2.close()

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert not errors, errors
    assert len(ids) == 2 and ids[0] == ids[1]
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
    assert verify_audit_chain(conn)["valid"] is True


def test_audit_hash_chain_valid_and_tamper_detected(conn):
    record_audit_event(conn, actor="a", action="X1", request={"k": 1}, correlation_id="C1")
    record_audit_event(conn, actor="a", action="X2", request={"k": 2}, correlation_id="C2")
    assert verify_audit_chain(conn)["valid"] is True
    conn.execute("UPDATE audit_events SET before_json='{\"evil\":1}' WHERE action='X2'")
    check = verify_audit_chain(conn)
    assert check["valid"] is False


def test_audit_sanitizes_sensitive_fields(conn):
    record_audit_event(conn, actor="a", action="SYNC",
                       request={"ts_token": "SECRET123"}, correlation_id="C1")
    row = conn.execute("SELECT request_json FROM audit_events").fetchone()[0]
    assert "SECRET123" not in row
    assert "[REDACTED]" in row


def test_audit_sanitizes_full_account_number(conn):
    """完整账户号禁止进入持久记录。"""
    record_audit_event(
        conn, actor="a", action="ACCOUNT",
        request={"account_no": "622202020011223344", "amount_fen": 100},
        correlation_id="C1",
    )
    row = conn.execute("SELECT request_json FROM audit_events").fetchone()[0]
    assert "622202020011223344" not in row
    assert "[REDACTED]" in row


def test_audit_sign_anchor_and_verify(conn, tmp_path: Path):
    record_audit_event(conn, actor="a", action="X", request={"k": 1}, correlation_id="C1")
    anchor_dir = tmp_path / "anchors"
    anchor = sign_chain_head(conn, anchor_dir, signing_key=TEST_KEY)
    assert verify_chain_head(conn, anchor, signing_key=TEST_KEY) is True
    conn.execute("UPDATE audit_events SET action='EVIL'")
    assert verify_chain_head(conn, anchor, signing_key=TEST_KEY) is False


def test_audit_signing_key_missing_refused(conn, tmp_path: Path, monkeypatch):
    """缺签名密钥 → 拒绝签名（INSUFFICIENT/ERROR 语义）。"""
    record_audit_event(conn, actor="a", action="X", request={"k": 1}, correlation_id="C1")
    monkeypatch.delenv("AUDIT_SIGNING_KEY", raising=False)
    with pytest.raises(AuditError, match="AUDIT_SIGNING_KEY_MISSING"):
        sign_chain_head(conn, tmp_path / "anchors")
    with pytest.raises(AuditError, match="AUDIT_SIGNING_KEY_MISSING"):
        verify_chain_head(conn, "does-not-matter", signing_key=None)


def test_audit_key_not_in_db_or_anchor(conn, tmp_path: Path):
    """签名密钥不得出现在 DB 或锚定文件中。"""
    record_audit_event(conn, actor="a", action="X", request={"k": 1}, correlation_id="C1")
    anchor_dir = tmp_path / "anchors"
    anchor = sign_chain_head(conn, anchor_dir, signing_key=TEST_KEY)
    anchor_text = Path(anchor).read_text(encoding="utf-8")
    assert TEST_KEY.decode() not in anchor_text
    rows = conn.execute(
        "SELECT request_json, before_json, after_json FROM audit_events"
    ).fetchall()
    blob = "|".join(str(r) for r in rows)
    assert TEST_KEY.decode() not in blob


def test_audit_signing_key_from_env(conn, tmp_path: Path, monkeypatch):
    """受忽略环境变量 AUDIT_SIGNING_KEY 可用作签名密钥来源。"""
    record_audit_event(conn, actor="a", action="X", request={"k": 1}, correlation_id="C1")
    monkeypatch.setenv("AUDIT_SIGNING_KEY", "env-secret-key")
    anchor = sign_chain_head(conn, tmp_path / "anchors")
    assert verify_chain_head(conn, anchor) is True
    assert "env-secret-key" not in Path(anchor).read_text(encoding="utf-8")
