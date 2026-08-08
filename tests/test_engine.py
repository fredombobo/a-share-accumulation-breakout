"""阶段4 验收：仿真撮合与会计处理。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from paper_trading.account import commit_import, create_account
from paper_trading.engine import execute_fills, expire_daily_orders
from paper_trading.orders import (
    confirm_order,
    create_buy_draft,
    create_sell_draft,
    get_order,
)

_TMP_DIRS: list[tempfile.TemporaryDirectory] = []


def _setup() -> str:
    """预置：账户(5000万分) + 日线 + 期初持仓 000001 200股。"""
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
            # 000001: 8/5 前收盘10.0, 8/6 开10.0高10.3低9.9收10.2, 8/7 正常开10.2
            ("000001.SZ", "20260805", 10.0, 10.2, 9.8, 10.0, 100000.0, 1000000.0),
            ("000001.SZ", "20260806", 10.0, 10.3, 9.9, 10.2, 120000.0, 1220000.0),
            ("000001.SZ", "20260807", 10.2, 10.5, 10.1, 10.4, 150000.0, 1550000.0),
            # 000002: 8/6 一字涨停（open=high=low=close=11.0, 前收10.0）
            ("000002.SZ", "20260805", 10.0, 10.2, 9.8, 10.0, 100000.0, 1000000.0),
            ("000002.SZ", "20260806", 11.0, 11.0, 11.0, 11.0, 50000.0, 550000.0),
            ("000002.SZ", "20260807", 11.0, 11.0, 11.0, 11.0, 80000.0, 880000.0),
            # 000003: 8/7 停牌（无 8/7 数据）
            ("000003.SZ", "20260805", 5.0, 5.1, 4.9, 5.0, 100000.0, 500000.0),
            ("000003.SZ", "20260806", 5.0, 5.2, 4.9, 5.1, 90000.0, 455000.0),
            # 000004: 8/7 低成交量（vol=1000，5% = 50 股 < 一手100 → 流动性不足）
            ("000004.SZ", "20260805", 8.0, 8.1, 7.9, 8.0, 100000.0, 800000.0),
            ("000004.SZ", "20260806", 8.0, 8.2, 7.9, 8.1, 50000.0, 405000.0),
            ("000004.SZ", "20260807", 8.1, 8.3, 8.0, 8.2, 1000.0, 8100.0),
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


def _add_signal(db: str, ts_code: str, trade_date: str = "20260806") -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO pt_signal_snapshot (trade_date, ts_code, pool,"
        " total_score, suggested_pos_pct, strategy_version, input_hash, available_at)"
        " VALUES (?,?,'A',80,10,'v1','h','20260806 15:30:00+08:00')",
        (trade_date, ts_code),
    )
    conn.commit()
    conn.close()


def _buy_and_confirm(db: str, ts_code: str, qty: int) -> str:
    _add_signal(db, ts_code)
    o = create_buy_draft(db, ts_code=ts_code, trade_date="20260806",
                         suggested_pos_pct=10.0, input_hash="h", qty=qty)
    confirm_order(db, o["order_id"], today="20260806")
    return o["order_id"]


def test_normal_fill_full():
    """次日正常开盘全额成交。"""
    db = _setup()
    oid = _buy_and_confirm(db, "000001.SZ", 100)
    assert get_order(db, oid)["state"] == "CONFIRMED"
    r = execute_fills(db, "20260807")
    filled = [f for f in r["filled"] if f["order_id"] == oid]
    assert len(filled) == 1
    f = filled[0]
    assert f["qty"] == 100
    # 买入成交价 = 开10.2 × (1+滑点10bp) = 10.2102
    assert abs(f["price_micro"] - 10_210_200) <= 10_000
    # 订单 FILLED
    assert get_order(db, oid)["state"] == "FILLED"
    # 持仓批次生成（T+1 可卖 8/10）
    conn = sqlite3.connect(db)
    lot = conn.execute(
        "SELECT remaining_qty, sellable_date FROM pt_position_lot WHERE buy_fill_id=?",
        (f["fill_id"],),
    ).fetchone()
    conn.close()
    assert lot == (100, "20260810")
    print(f"[PASS] 全额成交 qty={f['qty']} px={f['price_micro']} sellable={lot[1]}")


def test_limit_one_word_buy_zero_fill():
    """一字涨停买单 → 零成交。"""
    db = _setup()
    oid = _buy_and_confirm(db, "000002.SZ", 100)
    r = execute_fills(db, "20260807")
    z = [item for item in r["zero_fill"] if item["order_id"] == oid]
    assert z and z[0]["reason"] == "LIMIT_ONE_WORD_BUY"
    print("[PASS] 一字涨停买单零成交")


def test_stock_suspended_zero_fill():
    """停牌（无当日行情）→ 零成交，订单保留。"""
    db = _setup()
    oid = _buy_and_confirm(db, "000003.SZ", 100)
    r = execute_fills(db, "20260807")
    z = [z for z in r["zero_fill"] if z["ts_code"] == "000003.SZ"]
    assert z and z[0]["reason"] == "NO_QUOTE"
    # 订单未成交（保留 CONFIRMED 或 QUEUED）
    assert get_order(db, oid)["state"] in ("CONFIRMED", "QUEUED")
    print("[PASS] 停牌零成交订单保留")


def test_low_liquidity_zero_fill():
    """成交量 5% < 一手 → 零成交（流动性不足）。"""
    db = _setup()
    _buy_and_confirm(db, "000004.SZ", 100)
    r = execute_fills(db, "20260807")
    z = [z for z in r["zero_fill"] if z["ts_code"] == "000004.SZ"]
    assert z and z[0]["reason"] == "INSUFFICIENT_LIQUIDITY"
    print("[PASS] 低流动性零成交")


def test_sell_fifo_and_realized_pnl():
    """卖出：FIFO 核销批次 + 现金入账 + 已实现损益。"""
    db = _setup()
    # 期初 200 股（成本 10.0），卖 100 股 @ 8/7 开盘 10.2
    order = create_sell_draft(db, ts_code="000001.SZ", qty=100, today="20260806")
    confirm_order(db, order["order_id"], today="20260806")
    r = execute_fills(db, "20260807")
    f = next(x for x in r["filled"] if x["order_id"] == order["order_id"])
    assert f["qty"] == 100
    assert get_order(db, order["order_id"])["state"] == "FILLED"
    # 批次剩余 100
    conn = sqlite3.connect(db)
    remaining = conn.execute(
        "SELECT COALESCE(SUM(remaining_qty),0) FROM pt_position_lot WHERE ts_code='000001.SZ'"
    ).fetchone()[0]
    cash = conn.execute(
        "SELECT balance_fen FROM pt_cash_flow ORDER BY flow_id DESC LIMIT 1"
    ).fetchone()[0]
    conn.close()
    assert remaining == 100
    # 现金：初始5000万分 + 卖出(100×10.2×100分 - 费用)
    assert cash > 50_000_000
    print(f"[PASS] FIFO 核销 remaining={remaining} cash={cash}")


def test_full_sell_closes_position_lot_at_zero():
    """整批卖出后批次保留审计记录，剩余数量精确归零。"""
    db = _setup()
    order = create_sell_draft(db, ts_code="000001.SZ", qty=200, today="20260806")
    confirm_order(db, order["order_id"], today="20260806")

    result = execute_fills(db, "20260807")

    fill = next(item for item in result["filled"] if item["order_id"] == order["order_id"])
    assert fill["qty"] == 200
    conn = sqlite3.connect(db)
    remaining = conn.execute(
        "SELECT remaining_qty FROM pt_position_lot WHERE ts_code='000001.SZ'"
    ).fetchone()[0]
    conn.close()
    assert remaining == 0
    assert get_order(db, order["order_id"])["state"] == "FILLED"


def test_double_run_no_duplicate_fills():
    """同一交易日循环运行两次不产生重复成交。"""
    db = _setup()
    oid = _buy_and_confirm(db, "000001.SZ", 100)
    r1 = execute_fills(db, "20260807")
    n1 = len([f for f in r1["filled"] if f["order_id"] == oid])
    r2 = execute_fills(db, "20260807")
    n2 = len([f for f in r2["filled"] if f["order_id"] == oid])
    assert n1 == 1 and n2 == 0, "重复运行不得重复成交"
    conn = sqlite3.connect(db)
    n_fill = conn.execute("SELECT COUNT(*) FROM pt_fill WHERE order_id=?", (oid,)).fetchone()[0]
    conn.close()
    assert n_fill == 1
    print("[PASS] 重复运行无重复成交")


def test_fill_cash_reconciles_exact():
    """每笔成交现金变化可由成交额和费用逐项复算，误差为零分。"""
    db = _setup()
    oid = _buy_and_confirm(db, "000001.SZ", 100)
    r = execute_fills(db, "20260807")
    f = next(x for x in r["filled"] if x["order_id"] == oid)
    # 复算：买入扣款 = 成交额(px×qty×100) + 佣金 + 税(0) + 其他
    notional = f["price_micro"] / 1_000_000 * f["qty"] * 100
    commission = max(500, int(round(notional * 5 / 10_000)))
    other = int(round(notional * 1 / 10_000))
    total = int(round(notional)) + commission + other
    assert f["commission_fen"] == commission
    # 现金差额 = -total
    conn = sqlite3.connect(db)
    flows = conn.execute(
        "SELECT amount_fen FROM pt_cash_flow WHERE ref_id=? AND kind='BUY'",
        (f["fill_id"],),
    ).fetchall()
    conn.close()
    assert len(flows) == 1 and flows[0][0] == -total
    print(f"[PASS] 现金复算精确: notional={notional:.0f} commission={commission} total={total}")


def test_expire_daily_orders():
    """日终过期：CONFIRMED 未成交订单 → EXPIRED，释放预留。"""
    db = _setup()
    oid = _buy_and_confirm(db, "000004.SZ", 100)
    n = expire_daily_orders(db, "20260807")
    assert n >= 1
    o = get_order(db, oid)
    assert o["state"] == "EXPIRED" and o["reserve_fen"] == 0
    print("[PASS] 日终过期释放预留")
