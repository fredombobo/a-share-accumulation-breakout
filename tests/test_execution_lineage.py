"""P2.3 执行血缘测试：pt_fill 血缘列、版本标记、input hash、幂等迁移。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending, registered_ids
from paper_trading.migrations import current_schema_version

FILL_LINEAGE_COLUMNS = {
    "other_fee_fen", "fee_breakdown_json", "cost_version", "participation_bps",
    "quote_available_at", "input_hash", "rule_version",
}


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "lineage.db"
    from local_store import LocalStore

    LocalStore(db_path=path)  # 建 daily 基础表 + 运行 paper 迁移（含 M009）
    return str(path)


def _seed_cal_and_bar(db: str) -> None:
    from paper_trading.account import create_account

    create_account(db, 50_000_000)  # 50 万元
    conn = sqlite3.connect(db)
    from tests.paper_market_fixture import seed_fresh_neutral_benchmark

    seed_fresh_neutral_benchmark(conn)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (cal_date, is_open, source, updated_at)"
            " VALUES (?,?,?,?)",
            [("20260805", 1, "tushare", "t"), ("20260806", 1, "tushare", "t"),
             ("20260807", 1, "tushare", "t"), ("20260808", 0, "tushare", "t")],
        )
        conn.execute(
            "INSERT OR REPLACE INTO daily (ts_code, trade_date, open, high, low, close,"
            " pre_close, vol, amount, available_at, source, revision)"
            " VALUES ('000001.SZ','20260806',10.0,10.5,9.8,10.2,9.9,1000000,1e7,"
            " '2026-08-06T16:00:00+08:00','tushare',1)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO daily (ts_code, trade_date, open, high, low, close,"
            " pre_close, vol, amount, available_at, source, revision)"
            " VALUES ('000001.SZ','20260807',10.3,10.6,10.1,10.5,10.2,1200000,1.3e7,"
            " '2026-08-07T16:00:00+08:00','tushare',1)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO pt_signal_snapshot (trade_date, ts_code, pool,"
            " total_score, suggested_pos_pct, strategy_version, input_hash, available_at,"
            " tradeable)"
            " VALUES ('20260806','000001.SZ','A',80.0,10.0,'v1','hash123',"
            " '20260806 15:30:00+08:00',1)"
        )
        conn.commit()
    finally:
        conn.close()


def test_paper_migration_adds_lineage_columns(db: str):
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pt_fill)").fetchall()}
    assert FILL_LINEAGE_COLUMNS <= cols
    assert current_schema_version(db) >= 9


def test_v2_migration_registered_and_idempotent(db: str):
    assert "v2:execution_lineage" in registered_ids()
    with sqlite3.connect(db) as conn:
        apply_pending(conn)
    # paper M009 已加列；v2 意图再跑一次幂等（无新增列）
    with sqlite3.connect(db) as conn:
        assert apply_pending(conn) == []


def test_fill_carries_lineage_fields(db: str):
    """真实撮合后 pt_fill 行携带 fee_breakdown/版本/参与率/input hash。"""
    from paper_trading.engine import execute_fills
    from paper_trading.orders import confirm_order, create_buy_draft

    _seed_cal_and_bar(db)
    o = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                         suggested_pos_pct=10.0, input_hash="h", qty=100)
    confirm_order(db, o["order_id"], today="20260806")
    result = execute_fills(db, "20260807", today="20260807")
    assert result["filled"]
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT fee_breakdown_json, cost_version, participation_bps, input_hash,"
            " rule_version, other_fee_fen FROM pt_fill ORDER BY filled_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    breakdown = json.loads(row[0])
    assert set(breakdown) == {"commission_fen", "stamp_tax_fen", "other_fee_fen", "slippage_fen"}
    assert row[1] == "legacy-v1"          # cost_version：写路径未切换（dual-run 门）
    assert row[2] == 500                   # participation_bps 默认
    assert row[3].startswith("ORD-")       # input_hash = order_id:ts_code:trade_date
    assert row[4] == "v1"
    assert row[5] >= 0


def test_input_hash_deterministic_per_order_day(db: str):
    """同一订单同一天的 input_hash 确定（血缘可复现）。"""
    from paper_trading.engine import execute_fills
    from paper_trading.orders import confirm_order, create_buy_draft

    _seed_cal_and_bar(db)
    o = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                         suggested_pos_pct=10.0, input_hash="h", qty=100)
    confirm_order(db, o["order_id"], today="20260806")
    execute_fills(db, "20260807", today="20260807")
    with sqlite3.connect(db) as conn:
        hashes = [r[0] for r in conn.execute("SELECT input_hash FROM pt_fill").fetchall()]
    assert len(hashes) == 1
    assert hashes[0] == f"{o['order_id']}:000001.SZ:20260807"
