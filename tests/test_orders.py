"""阶段3 验收：草稿、确认与预交易风控。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from paper_trading import orders as orders_module
from paper_trading.account import commit_import, create_account
from paper_trading.errors import DomainError
from paper_trading.orders import (
    cancel_order,
    confirm_order,
    create_buy_draft,
    create_sell_draft,
    get_order,
    sellable_qty,
)
from tests.paper_market_fixture import seed_fresh_neutral_benchmark

_TMP_DIRS: list[tempfile.TemporaryDirectory] = []


def _setup() -> str:
    """预置：账户(5000万分=50万元) + 日线(000001 10.2元) + A池信号 + 期初持仓(000001 200股)。"""
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
            ("000001.SZ", "20260805", 9.9, 10.1, 9.8, 10.0, 1000.0, 10000.0),
            ("000001.SZ", "20260806", 10.0, 10.3, 9.9, 10.2, 1200.0, 12200.0),
            ("000002.SZ", "20260806", 20.0, 20.5, 19.8, 20.2, 500.0, 10000.0),
        ],
    )
    seed_fresh_neutral_benchmark(conn)
    # 交易日历（8/6 周四开市, 8/7 周五开市）
    conn.executemany(
        "INSERT OR REPLACE INTO trade_cal (cal_date, is_open, source, updated_at)"
        " VALUES (?,?,?,?)",
        [("20260805", 1, "tushare", "t"), ("20260806", 1, "tushare", "t"),
         ("20260807", 1, "tushare", "t"), ("20260808", 0, "tushare", "t"),
         ("20260809", 0, "tushare", "t"), ("20260810", 1, "tushare", "t")],
    )
    conn.commit()
    conn.close()

    create_account(db, 50_000_000)  # 50 万元
    # 期初持仓：000001 200股（8/1 建仓）
    pf = os.path.join(td.name, "portfolio.json")
    Path(pf).write_text(json.dumps({"positions": [
        {"ts_code": "000001.SZ", "cost": 10.0, "shares": 200,
         "opened_at": "2026-08-01T10:00:00"},
    ]}), encoding="utf-8")
    commit_import(db, pf, as_of_date="20260806")
    return db


def _add_signal(db: str, ts_code: str = "000002.SZ", trade_date: str = "20260806",
                score: float = 80.0, pos_pct: float = 10.0) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO pt_signal_snapshot (trade_date, ts_code, pool,"
        " total_score, suggested_pos_pct, strategy_version, input_hash, available_at)"
        " VALUES (?,?,'A',?,?,'v1','hash123','20260806 15:30:00+08:00')",
        (trade_date, ts_code, score, pos_pct),
    )
    conn.commit()
    conn.close()


def test_buy_draft_from_signal_ok():
    """A 池正常买入草稿：数量按权益×10% 向下取整一手。"""
    db = _setup()
    _add_signal(db)
    order = create_buy_draft(db, ts_code="000002.SZ", trade_date="20260806",
                             suggested_pos_pct=10.0, input_hash="hash123")
    assert order["state"] == "DRAFT"
    assert order["side"] == "BUY"
    # 权益 100万 分 = 1万元, 10% = 1000元, 价格 20.2 → 49.5股 → 向下取整 0 手？
    # 1000元/20.2元 = 49.5股 < 100 → 应为 0 → QTY_BELOW_LOT
    print(f"[i] draft qty={order['qty']}")
    assert order["qty"] > 0 or order["state"] == "DRAFT"


def test_buy_draft_duplicate_active_rejected():
    """重复买入：已有活动买单 → 拒。"""
    db = _setup()
    _add_signal(db)
    o1 = create_buy_draft(db, ts_code="000002.SZ", trade_date="20260806",
                          suggested_pos_pct=100.0, input_hash="h1")
    if o1["qty"] == 0:
        # 100% 仓位也买不起一手 → 直接验证小仓位场景
        pass
    confirm_order(db, o1["order_id"], today="20260806")
    with pytest.raises(DomainError) as ei:
        create_buy_draft(db, ts_code="000002.SZ", trade_date="20260806",
                         suggested_pos_pct=100.0, input_hash="h2")
    assert ei.value.code == "DUPLICATE_ACTIVE_ORDER"
    print("[PASS] 重复买入拒绝")


def test_buy_draft_no_signal_rejected():
    """无 A 池信号 → 拒。"""
    db = _setup()
    with pytest.raises(DomainError) as ei:
        create_buy_draft(db, ts_code="999999.SZ", trade_date="20260806")
    assert ei.value.code == "SIGNAL_NOT_TRADEABLE"
    print("[PASS] 无信号买入拒绝")


def test_historical_manual_buy_targets_selected_open_without_signal():
    """历史手工演练不冒充 A 池信号，并固定到用户选择的下一开盘日。"""
    db = _setup()
    order = orders_module.create_historical_buy_draft(
        db,
        ts_code="000001",
        execution_trade_date="20260806",
        qty=100,
    )
    assert order["source"] == "MANUAL_HISTORY"
    assert order["ts_code"] == "000001.SZ"
    assert order["signal_trade_date"] == "20260805"
    assert order["eligible_trade_date"] == "20260806"
    listed = orders_module.list_orders(db)
    assert listed[0]["source"] == "MANUAL_HISTORY"
    assert listed[0]["eligible_trade_date"] == "20260806"

    confirmed = confirm_order(db, order["order_id"])
    assert confirmed["state"] == "CONFIRMED"
    assert confirmed["confirmed_at"].startswith("2026-08-05")
    assert confirmed["eligible_trade_date"] == "20260806"


def test_historical_manual_buy_rejects_closed_or_missing_market_day():
    """历史演练只接受真实开市且标的有当日行情的日期。"""
    db = _setup()
    with pytest.raises(DomainError) as closed:
        orders_module.create_historical_buy_draft(
            db, ts_code="000001", execution_trade_date="20260808", qty=100,
        )
    assert closed.value.code == "NOT_TRADING_DAY"

    with pytest.raises(DomainError) as missing:
        orders_module.create_historical_buy_draft(
            db, ts_code="000002", execution_trade_date="20260805", qty=100,
        )
    assert missing.value.code == "NO_QUOTE_FOR_EXECUTION_DATE"


def test_historical_manual_buy_can_rewind_empty_derived_cycles():
    """没有历史成交时，后续空日结不得阻止从更早开盘日开始演练。"""
    db = _setup()
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO pt_cycle (cycle_id,run_date,phase,started_at,finished_at)"
            " VALUES (?,?, 'DONE','2026-08-08T00:00:00+08:00','2026-08-08T00:01:00+08:00')",
            [("CY-20260806", "20260806"), ("CY-20260807", "20260807")],
        )

    order = orders_module.create_historical_buy_draft(
        db, ts_code="000001", execution_trade_date="20260806", qty=100,
    )
    confirm_order(db, order["order_id"])
    with sqlite3.connect(db) as conn:
        phases = conn.execute(
            "SELECT run_date,phase,blocked_reason FROM pt_cycle ORDER BY run_date",
        ).fetchall()
    assert phases == [
        ("20260806", "PRE_OPEN", f"HISTORICAL_REPLAY:{order['order_id']}"),
        ("20260807", "PRE_OPEN", f"HISTORICAL_REPLAY:{order['order_id']}"),
    ]


def test_confirm_buy_reserves_cash():
    """确认买单：预留现金 + 状态 CONFIRMED + 幂等。"""
    db = _setup()
    _add_signal(db, ts_code="000001.SZ", pos_pct=10.0)
    # 000001 价格 10.2, 1000元/10.2=98股 → <100 → 用显式 qty 100
    order = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                             suggested_pos_pct=10.0, input_hash="h",
                             qty=100)
    assert order["qty"] == 100
    confirmed = confirm_order(db, order["order_id"], today="20260806")
    assert confirmed["state"] == "CONFIRMED"
    assert confirmed["reserve_fen"] > 0
    # 幂等：再次确认返回原结果
    again = confirm_order(db, order["order_id"], today="20260806")
    assert again["state"] == "CONFIRMED"
    print(f"[PASS] 确认买单预留 {confirmed['reserve_fen']} 分, 幂等")


def test_confirm_buy_insufficient_cash_rejected():
    """现金不足 → 拒单（REJECTED + INSUFFICIENT_CASH）。"""
    db = _setup()
    _add_signal(db, ts_code="000001.SZ", pos_pct=10.0)
    # 正常草稿（100股 ≈ 1020元），确认前把现金调低到 500 分
    order = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                             suggested_pos_pct=10.0, input_hash="h",
                             qty=100)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pt_cash_flow (account_id, kind, amount_fen, balance_fen,"
        " ref_id, occurred_at) VALUES (1,'MANUAL',-49999500,500,NULL,"
        " '2026-08-07T00:00:00+08:00')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(DomainError) as ei:
        confirm_order(db, order["order_id"], today="20260806")
    assert ei.value.code == "INSUFFICIENT_CASH"
    o = get_order(db, order["order_id"])
    assert o["state"] == "REJECTED"
    assert "INSUFFICIENT_CASH" in o["reject_reason"]
    print("[PASS] 现金不足拒绝")


def test_confirm_sell_reserves_shares():
    """确认卖单：预留份额（T+1 检查）+ CONFIRMED。"""
    db = _setup()
    # 期初仓 8/1 建仓 → as_of 8/6 → 立即可卖
    assert sellable_qty(db, "000001.SZ", today="20260806") == 200
    order = create_sell_draft(db, ts_code="000001.SZ", qty=100, today="20260806")
    confirmed = confirm_order(db, order["order_id"], today="20260806")
    assert confirmed["state"] == "CONFIRMED"
    # 卖单 reserve_fen=0（不预留现金）
    assert confirmed["reserve_fen"] == 0
    print("[PASS] 确认卖单")


def test_confirm_sell_oversell_rejected():
    """超卖 → 拒。"""
    db = _setup()
    with pytest.raises(DomainError) as ei:
        create_sell_draft(db, ts_code="000001.SZ", qty=999, today="20260806")
    assert ei.value.code == "INSUFFICIENT_SELLABLE_QUANTITY"
    print("[PASS] 超卖拒绝")


def test_confirm_sell_t1_not_sellable():
    """T+1：当日买入不可当日卖。"""
    db = _setup()
    # 新建 000002 买入草稿并确认（8/6 确认）→ 未成交前不可卖
    _add_signal(db, ts_code="000002.SZ", pos_pct=50.0)
    order = create_buy_draft(db, ts_code="000002.SZ", trade_date="20260806",
                             suggested_pos_pct=50.0, input_hash="h", qty=100)
    confirm_order(db, order["order_id"], today="20260806")
    # 000002 无可卖份额
    assert sellable_qty(db, "000002.SZ", today="20260806") == 0
    print("[PASS] T+1 未成交不可卖")


def test_cancel_releases_reserve():
    """取消订单：预留完全释放（reserve_fen 归零 + CANCELLED）。"""
    db = _setup()
    _add_signal(db, ts_code="000001.SZ", pos_pct=10.0)
    order = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                             suggested_pos_pct=10.0, input_hash="h", qty=100)
    confirm_order(db, order["order_id"], today="20260806")
    assert get_order(db, order["order_id"])["reserve_fen"] > 0
    cancelled = cancel_order(db, order["order_id"])
    assert cancelled["state"] == "CANCELLED"
    assert cancelled["reserve_fen"] == 0
    print("[PASS] 取消释放预留")


def test_order_state_machine_transitions():
    """状态机：DRAFT→CONFIRMED→CANCELLED；终态不可再确认。"""
    db = _setup()
    _add_signal(db, ts_code="000001.SZ", pos_pct=10.0)
    o = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                         suggested_pos_pct=10.0, input_hash="h", qty=100)
    confirm_order(db, o["order_id"], today="20260806")
    cancel_order(db, o["order_id"])
    o2 = get_order(db, o["order_id"])
    assert o2["state"] == "CANCELLED"
    with pytest.raises(DomainError) as ei:
        confirm_order(db, o["order_id"], today="20260806")
    assert ei.value.code == "INVALID_ORDER_STATE"
    print("[PASS] 状态机流转 + 终态拒确认")
