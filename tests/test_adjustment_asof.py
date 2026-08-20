"""P1.3 公司行为/复权测试：账本只追加、冲正、as-of 复权、日结阻断。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.application.corporate_action_service import (
    CorporateActionError,
    blocking_summary,
    ingest_dividend,
    ingest_split,
    reversal,
)
from ab_screener.data.corporate_action_repository import (
    adj_factor_asof,
    has_unprocessed_for,
    mark_applied,
    pending_actions,
)
from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.pit_writer import write_plain


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "ca.db"
    conn = sqlite3.connect(str(path))
    try:
        apply_pending(conn)
    finally:
        conn.close()
    return str(path)


def test_ingest_idempotent_and_pending(db: str):
    a1 = ingest_dividend(db, ts_code="000001.SZ", ex_date="20260710", cash_div_fen=250)
    a2 = ingest_dividend(db, ts_code="000001.SZ", ex_date="20260710", cash_div_fen=250)
    assert a1 == a2  # 幂等
    with sqlite3.connect(db) as conn:
        pending = pending_actions(conn, ts_codes=["000001.SZ"], as_of="20260801")
    assert len(pending) == 1 and pending[0]["kind"] == "DIVIDEND"
    assert pending[0]["payload"]["cash_div_fen"] == 250


def test_ledger_append_only_blocks_update_delete(db: str):
    ingest_split(db, ts_code="600000.SH", ex_date="20260715", ratio=2.0)
    with sqlite3.connect(db) as conn:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE corporate_actions SET payload_json='{}' WHERE corporate_action_id=1")
        conn.rollback()
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM corporate_actions WHERE corporate_action_id=1")
        conn.rollback()


def test_reversal_is_append_only_correction(db: str):
    aid = ingest_dividend(db, ts_code="000001.SZ", ex_date="20260710", cash_div_fen=250)
    rid = reversal(db, original_id=aid, payload={"cash_div_fen": 180, "reason": "更正"})
    with sqlite3.connect(db) as conn:
        # 账本两行：原事件 + REVERSAL（内容不可改）
        kinds = [r[0] for r in conn.execute(
            "SELECT kind FROM corporate_actions ORDER BY corporate_action_id").fetchall()]
        assert kinds == ["DIVIDEND", "REVERSAL"]
        rev = conn.execute(
            "SELECT reversal_of FROM corporate_actions WHERE corporate_action_id=?",
            (rid,),
        ).fetchone()
        assert rev[0] == aid
        status = conn.execute(
            "SELECT status FROM corporate_action_status WHERE corporate_action_id=?",
            (aid,),
        ).fetchone()
        assert status[0] == "REVERSED"
        # 重复冲正拒绝
        with pytest.raises(CorporateActionError, match="已冲正"):
            reversal(db, original_id=aid, payload={"cash_div_fen": 100})


def test_adj_factor_asof_from_pit_history(db: str):
    conn = sqlite3.connect(db)
    try:
        write_plain(
            conn, "adj_factor",
            [{"ts_code": "000001.SZ", "trade_date": "20260701", "adj_factor": 1.05}],
            source="tushare", available_at="2026-07-01T16:00:00+08:00", partition_key="20260701",
        )
    finally:
        conn.close()
    with sqlite3.connect(db) as conn:
        factor = adj_factor_asof(conn, "000001.SZ", "2026-07-02T00:00:00+08:00")
        assert factor == 1.05
        with pytest.raises(CorporateActionError, match="复权因子"):
            adj_factor_asof(conn, "000001.SZ", "2026-06-30T00:00:00+08:00")


def test_blocking_summary_and_clear(db: str):
    ingest_dividend(db, ts_code="000001.SZ", ex_date="20260710", cash_div_fen=250)
    summary = blocking_summary(db, ["000001.SZ"], "20260801")
    assert summary["blocked"] is True and summary["count"] == 1
    # 无持仓 → 不阻断
    assert blocking_summary(db, [], "20260801")["blocked"] is False
    # 处理后解除
    with sqlite3.connect(db) as conn:
        pending = pending_actions(conn, ts_codes=["000001.SZ"], as_of="20260801")
        mark_applied(conn, pending[0]["corporate_action_id"])
    assert blocking_summary(db, ["000001.SZ"], "20260801")["blocked"] is False
    with sqlite3.connect(db) as conn:
        assert has_unprocessed_for(conn, ["000001.SZ"], "20260801") is False


def test_missing_table_gate_inactive(tmp_path: Path):
    """账本未迁移 → 门禁未激活（legacy 路径不变），写入路径仍 fail-closed。"""
    empty = tmp_path / "naked.db"
    sqlite3.connect(str(empty)).close()
    summary = blocking_summary(empty, ["000001.SZ"], "20260801")
    assert summary["blocked"] is False and summary["gate_active"] is False
    with pytest.raises(CorporateActionError, match="表不存在"):
        ingest_dividend(empty, ts_code="000001.SZ", ex_date="20260710", cash_div_fen=250)


def test_migration_registered():
    from ab_screener.data.migration_registry import registered_ids

    assert "v2:corporate_actions" in registered_ids()
