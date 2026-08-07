"""阶段5 验收：日结、估值和对账。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trading.account import commit_import, create_account  # noqa: E402
from paper_trading.engine import execute_fills  # noqa: E402
from paper_trading.orders import (  # noqa: E402
    confirm_order,
    create_buy_draft,
    create_sell_draft,
    get_order,
)
from paper_trading.settlement import (  # noqa: E402
    get_positions,
    mark_to_market,
    run_reconciliation,
    run_settlement,
)
from paper_trading.migrations import run_migrations  # noqa: E402

_TMP_DIRS: list[tempfile.TemporaryDirectory] = []


def _setup() -> str:
    td = tempfile.TemporaryDirectory()
    _TMP_DIRS.append(td)
    db = os.path.join(td.name, "stock_data.db")
    from local_store import LocalStore
    LocalStore(db_path=db)
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT OR IGNORE INTO daily (ts_code, trade_date, open, high, low, close, vol, amount)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [
            ("000001.SZ", "20260805", 10.0, 10.2, 9.8, 10.0, 100000.0, 1000000.0),
            ("000001.SZ", "20260806", 10.0, 10.3, 9.9, 10.2, 120000.0, 1220000.0),
            ("000001.SZ", "20260807", 10.2, 10.5, 10.1, 10.4, 150000.0, 1550000.0),
        ],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO trade_cal (cal_date, is_open, source, updated_at)"
        " VALUES (?,?,?,?)",
        [("20260805", 1, "tushare", "t"), ("20260806", 1, "tushare", "t"),
         ("20260807", 1, "tushare", "t"), ("20260808", 0, "tushare", "t"),
         ("20260810", 1, "tushare", "t")],
    )
    conn.commit()
    conn.close()
    create_account(db, 50_000_000)
    pf = os.path.join(td.name, "portfolio.json")
    Path(pf).write_text(json.dumps({"positions": [
        {"ts_code": "000001.SZ", "cost": 10.0, "shares": 200,
         "opened_at": "2026-08-01T10:00:00"},
    ]}), encoding="utf-8")
    commit_import(db, pf, as_of_date="20260806")
    return db


def _add_signal(db: str, ts_code: str = "000001.SZ") -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO pt_signal_snapshot (trade_date, ts_code, pool,"
        " total_score, suggested_pos_pct, strategy_version, input_hash, available_at)"
        " VALUES ('20260806',?,'A',80,10,'v1','h','20260806 15:30:00+08:00')",
        (ts_code,),
    )
    conn.commit()
    conn.close()


def test_mark_to_market_valuation():
    """按收盘估值：现金 + 市值 + 未实现损益。"""
    db = _setup()
    # 期初 200 股成本 10.0，8/7 收盘 10.4
    mark = mark_to_market(db, "20260807")
    assert mark["cash_fen"] == 50_000_000
    # 200 股 × 10.4 元 = 2080 元 = 208000 分
    assert mark["market_value_fen"] == 208_000, mark
    assert mark["total_asset_fen"] == 50_208_000
    # 未实现 = 2080 - 2000 = 80 元 = 8000 分
    assert mark["unrealized_pnl_fen"] == 8000, mark
    print(f"[PASS] 估值: cash={mark['cash_fen']} mv={mark['market_value_fen']} unreal={mark['unrealized_pnl_fen']}")


def test_reconciliation_normal_ok():
    """正常日结对账差异为零。"""
    db = _setup()
    rec = run_reconciliation(db, "20260807")
    assert rec["result"] == "OK", rec["diffs"]
    assert rec["diffs"] == []
    print("[PASS] 正常对账差异为零")


def test_reconciliation_catches_cash_diff():
    """人工制造一分钱现金差异 → 对账失败并定位。"""
    db = _setup()
    # 注入一条金额与余额不一致的流水（sum=50000001 ≠ last balance=50000000）
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pt_cash_flow (account_id, kind, amount_fen, balance_fen,"
        " ref_id, occurred_at) VALUES (1,'MANUAL',1,50000000,'manipulate',"
        " '2026-08-07T00:00:00+08:00')"
    )
    conn.commit()
    conn.close()
    rec = run_reconciliation(db, "20260807")
    assert rec["result"] == "DIFF"
    rules = [d["rule"] for d in rec["diffs"]]
    assert "CASH_FLOW_SUM_MISMATCH" in rules, rules
    print("[PASS] 一分钱现金差异被对账捕获")


def test_reconciliation_catches_oversold():
    """人工制造超卖记录 → 对账失败。"""
    db = _setup()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
        " before_json, after_json, occurred_at)"
        " VALUES ('system','FIFO_CONSUME','fill','x',NULL,"
        " '{\"short\":50}', '2026-08-07T00:00:00+08:00')"
    )
    conn.commit()
    conn.close()
    rec = run_reconciliation(db, "20260807")
    assert rec["result"] == "DIFF"
    rules = [d["rule"] for d in rec["diffs"]]
    assert "OVERSOLD" in rules
    print("[PASS] 超卖记录被对账捕获")


def test_settlement_full_flow():
    """完整日结：撮合 + 估值 + 对账 + 固化快照。"""
    db = _setup()
    _add_signal(db)
    # 买入 100 股 000001（确认于 8/6，8/7 撮合）
    o = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                         suggested_pos_pct=10.0, input_hash="h", qty=100)
    confirm_order(db, o["order_id"], today="20260806")
    r = run_settlement(db, "20260807", today="20260807")
    assert r["filled_count"] == 1
    assert r["snapshot_ok"] is True
    assert r["mark"]["trade_date"] == "20260807"
    # 快照固化
    conn = sqlite3.connect(db)
    snap = conn.execute(
        "SELECT cash_fen, market_value_fen, total_asset_fen, positions_json"
        " FROM pt_daily_snapshot WHERE trade_date='20260807'"
    ).fetchone()
    cycle = conn.execute("SELECT phase FROM pt_cycle WHERE run_date='20260807'").fetchone()
    conn.close()
    assert snap is not None
    assert cycle == ("DONE",)
    assert r["mark"]["total_asset_fen"] == snap[2]
    print(f"[PASS] 日结全流程: filled={r['filled_count']} total={snap[2]} phase={cycle[0]}")


def test_settlement_cannot_complete_with_diff():
    """阻断差异存在时日结不得标记完成（快照不写 / 对账 DIFF）。"""
    db = _setup()
    _add_signal(db)
    o = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                         suggested_pos_pct=10.0, input_hash="h", qty=100)
    confirm_order(db, o["order_id"], today="20260806")
    # 先制造现金差异（sum ≠ last balance）
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pt_cash_flow (account_id, kind, amount_fen, balance_fen,"
        " ref_id, occurred_at) VALUES (1,'MANUAL',1,50000000,'manip',"
        " '2026-08-07T00:00:00+08:00')"
    )
    conn.commit()
    conn.close()
    r = run_settlement(db, "20260807", today="20260807")
    assert r["reconciliation"]["result"] == "DIFF"
    assert r["snapshot_ok"] is False
    print("[PASS] 阻断差异 → 快照不标记完成")
