"""P2.1 执行核心测试：撮合/费用/tick/滑点/FIFO/T+1/参与率/拒绝语义。"""
from __future__ import annotations

import pytest

from ab_screener.domain.execution.fill_model import FillRequest, compute_fill
from ab_screener.domain.execution.market_rules import (
    can_trade,
    floor_to_lot,
    participation_max_qty,
    slipped_price_micro,
    tick_round_micro,
)
from ab_screener.domain.execution.models import (
    EXECUTION_MODEL_VERSION,
    MoneyError,
    Quote,
    require_int_fen,
)
from ab_screener.domain.execution.settlement_rules import (
    Lot,
    consume_fifo_lots,
    next_sellable_date,
)


def _quote(**over) -> Quote:
    base = {
        "ts_code": "000001.SZ", "trade_date": "20260810",
        "open_micro": 10_000_000, "high_micro": 10_500_000, "low_micro": 9_800_000,
        "close_micro": 10_200_000, "vol": 1_000_000, "amount_fen": 10_000_000,
        "pre_close_micro": 9_900_000, "available_at": "2026-08-10T16:00:00+08:00",
    }
    base.update(over)
    return Quote(**base)


def test_standard_buy_fill_exact():
    """标准买入：整手、价格=开盘+滑点、费用拆解、现金变动。"""
    q = _quote(open_micro=10_000_000, high_micro=10_300_000, low_micro=9_900_000)
    fill = compute_fill(q, FillRequest(ts_code="000001.SZ", side="BUY",
                                       trade_date="20260810", input_hash="h1",
                                       cash_available_fen=1_000_000_00))
    assert fill.filled is True
    assert fill.qty % 100 == 0 and fill.qty > 0
    # 滑点后价 = 10.00 × 1.001 = 10.01（钳制在 [9.90, 10.30] 内，tick 0.01）
    assert fill.price_micro == 10_010_000
    notional = fill.qty * 10_010_000 // 10_000   # 1 分 = 10000 微元
    assert fill.notional_fen == notional
    assert fill.fees.commission_fen >= 500
    assert fill.fees.stamp_tax_fen == 0            # 买入无印花税
    assert fill.cash_delta_fen < 0
    assert fill.model_version == EXECUTION_MODEL_VERSION


def test_sell_fill_stamp_tax_and_fifo():
    q = _quote(open_micro=10_000_000, high_micro=10_500_000, low_micro=9_800_000)
    fill = compute_fill(q, FillRequest(ts_code="000001.SZ", side="SELL",
                                       trade_date="20260810", input_hash="h2",
                                       position_qty=1000))
    assert fill.filled is True
    assert fill.fees.stamp_tax_fen > 0             # 卖出印花税
    assert fill.cash_delta_fen > 0


def test_zero_fill_semantics():
    # 无报价
    assert compute_fill(_quote(open_micro=0), FillRequest(
        ts_code="x", side="BUY", trade_date="d", input_hash="h")).reason == "NO_QUOTE"
    # 无量
    assert compute_fill(_quote(vol=0), FillRequest(
        ts_code="x", side="BUY", trade_date="d", input_hash="h")).reason == "NO_VOLUME"
    # 一字涨停买
    up = 10_890_000  # 9.90 × 1.10
    one_side = _quote(open_micro=up, high_micro=up, low_micro=up, pre_close_micro=9_900_000)
    assert compute_fill(one_side, FillRequest(
        ts_code="x", side="BUY", trade_date="d", input_hash="h")).reason == "LIMIT_UP_ONE_SIDE"


def test_participation_sizing_and_lot_floor():
    # 5% 参与率：100万股 → 5万 → 整手
    assert participation_max_qty(1_000_000, 500) == 50_000
    assert floor_to_lot(50_123, 100) == 50_100
    assert floor_to_lot(99, 100) == 0


def test_slippage_clamped_and_tick():
    q = _quote(open_micro=10_000_000, high_micro=10_050_000, low_micro=9_900_000)
    p = slipped_price_micro(10_000_000, "BUY", q, 10)
    assert p <= 10_050_000  # 钳制在高点内
    assert p % 10_000 == 0  # tick 0.01
    assert tick_round_micro(10_005_000) == 10_010_000


def test_can_trade_limits():
    up = 10_890_000
    one_side = _quote(open_micro=up, high_micro=up, low_micro=up, pre_close_micro=9_900_000)
    ok, reason = can_trade(one_side, "BUY")
    assert not ok and reason == "LIMIT_UP_ONE_SIDE"
    ok, _ = can_trade(one_side, "SELL")
    assert ok
    lo = 8_910_000  # 9.90 × 0.90 一字跌停
    down_side = _quote(open_micro=lo, high_micro=lo, low_micro=lo, pre_close_micro=9_900_000)
    ok, reason = can_trade(down_side, "SELL")
    assert not ok and reason == "LIMIT_DOWN_ONE_SIDE"


def test_fifo_consume_and_oversold_reject():
    lots = [
        Lot(lot_id=1, ts_code="000001.SZ", qty=300, cost_price_micro=10_000_000,
            sellable_date="20260811"),
        Lot(lot_id=2, ts_code="000001.SZ", qty=200, cost_price_micro=11_000_000,
            sellable_date="20260812"),
    ]
    result = consume_fifo_lots(lots, 400, sell_price_micro=12_000_000)
    assert result.consumed == [(1, 300), (2, 100)]
    assert result.remainder_qty == 0
    # 盈亏 = (12-10)*300 + (12-11)*100 = 600 + 100 = 700 元 = 70000 分
    assert result.realized_pnl_fen == 70_000
    with pytest.raises(MoneyError, match="超卖"):
        consume_fifo_lots(lots, 501, sell_price_micro=12_000_000)


def test_t_plus_1_sellable_date():
    assert next_sellable_date("20260810", ["20260810", "20260811", "20260812"]) == "20260811"
    assert next_sellable_date("20260812", ["20260810"]) is None


def test_float_money_input_rejected():
    with pytest.raises(MoneyError, match="整数分"):
        require_int_fen(10.5, name="cash")
    with pytest.raises(MoneyError, match="整数分"):
        FillRequest(ts_code="x", side="BUY", trade_date="d", input_hash="h",
                    cash_available_fen=10.5)
    with pytest.raises(MoneyError, match="整数微元"):
        _quote(open_micro=10_000_000.0)  # float 价格


def test_duplicate_fill_guard_input_hash_required():
    with pytest.raises(MoneyError, match="input_hash"):
        FillRequest(ts_code="x", side="BUY", trade_date="d", input_hash="")


def test_buy_with_insufficient_cash_zero_fill():
    q = _quote(open_micro=10_000_000, high_micro=10_300_000, low_micro=9_900_000)
    fill = compute_fill(q, FillRequest(ts_code="x", side="BUY", trade_date="d",
                                       input_hash="h", cash_available_fen=500))  # 5 元
    assert fill.filled is False and fill.reason == "INSUFFICIENT_CASH"
