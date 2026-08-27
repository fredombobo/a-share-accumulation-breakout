"""P2.1 金额精确性测试：整数分逐项复算、与旧核心 dual-run 对比、费用版本拒绝。"""

from __future__ import annotations

import pytest

from ab_screener.domain.execution.dual_run import compare_round_trip
from ab_screener.domain.execution.fees import (
    FeeParams,
    commission_fen,
    compute_fees,
    fee_version,
    other_fee_fen,
    stamp_tax_fen,
)
from ab_screener.domain.execution.fill_model import FillRequest, compute_fill
from ab_screener.domain.execution.models import MoneyError
from ab_screener.domain.execution.settlement_rules import Lot, consume_fifo_lots


def test_commission_minimum_exact():
    # 名义 1000 元 = 100000 分：万五 → 50 分 < 最低 500 分 → 500
    assert commission_fen(100_000, FeeParams()) == 500
    # 名义 200000 分 = 2000 元：万五 → 100 分 < 500 → 500
    assert commission_fen(200_000, FeeParams()) == 500
    # 名义 2000_0000 分 = 20 万元：万五 → 10000 分（100 元）
    assert commission_fen(20_000_000, FeeParams()) == 10_000


def test_stamp_and_other_fee_exact():
    params = FeeParams()
    # 卖出名义 2000 万元分 = 20 万元：印花千一 → 20000 分（200 元）
    assert stamp_tax_fen(20_000_000, params) == 20_000
    # 其他费万一 → 2000 分（20 元）
    assert other_fee_fen(20_000_000, params) == 2_000


def test_fee_breakdown_sell_hand_calc():
    """手算：名义 10_000_00 分（1 万元）卖出。"""
    notional = 1_000_000  # 1 万元
    params = FeeParams()
    fees = compute_fees(notional, "SELL", params, slippage_notional_fen=10_000)
    assert fees.commission_fen == max(500, (1_000_000 * 5 + 5000) // 10_000)  # 505
    assert fees.stamp_tax_fen == (1_000_000 * 10 + 5000) // 10_000  # 1000
    assert fees.other_fee_fen == (1_000_000 * 1 + 5000) // 10_000  # 100
    assert fees.slippage_fen == (10_000 * 10 + 5000) // 10_000  # 10
    # 买入无印花税
    buy_fees = compute_fees(notional, "BUY", params, slippage_notional_fen=0)
    assert buy_fees.stamp_tax_fen == 0


def test_unknown_fee_version_rejected():
    with pytest.raises(MoneyError, match="未知费用版本"):
        FeeParams(version="v0")
    assert fee_version() == "v2-fixed-2026-08-18"


def test_dual_run_parity_with_legacy_core():
    """同一标准场景：v2 与旧 costs 原语在相同数量下价格/费用零差异。"""
    from ab_screener.domain.costs import apply_slippage, commission_for, other_fee_for
    from ab_screener.domain.execution.models import Quote

    q = Quote(
        ts_code="000001.SZ",
        trade_date="20260810",
        open_micro=10_000_000,
        high_micro=10_500_000,
        low_micro=9_800_000,
        close_micro=10_200_000,
        vol=1_000_000,
        amount_fen=10_000_000,
        pre_close_micro=9_900_000,
    )
    v2_fill = compute_fill(
        q,
        FillRequest(
            ts_code="000001.SZ",
            side="BUY",
            trade_date="20260810",
            input_hash="parity",
            cash_available_fen=2_000_000_00,
        ),
    )

    # 旧核心买入原语（同一开盘价）
    legacy_px = apply_slippage(10.0, side="buy", high=10.5, low=9.8)
    assert v2_fill.price_micro == int(round(legacy_px * 1_000_000))
    actual_slippage_fen = abs(v2_fill.price_micro - q.open_micro) * v2_fill.qty // 10_000
    assert v2_fill.fees.slippage_fen == actual_slippage_fen

    # 同数量下的费用对比
    legacy_notional = legacy_px * v2_fill.qty
    legacy_comm_fen = int(round(commission_for(legacy_notional) * 100))
    legacy_other_fen = int(round(other_fee_for(legacy_notional) * 100))
    assert abs(v2_fill.fees.commission_fen - legacy_comm_fen) <= 1
    assert abs(v2_fill.fees.other_fee_fen - legacy_other_fen) <= 1

    result = compare_round_trip(
        v2_fill.to_dict(),
        {
            "qty": v2_fill.qty,
            "price": legacy_px,
            "commission": legacy_comm_fen / 100,
            "stamp_tax": 0.0,
            "other_fee": legacy_other_fen / 100,
        },
    )
    assert result["parity"] is True, result["diffs"]


def test_fifo_realized_pnl_exact_fen():
    lots = [
        Lot(lot_id="L1", ts_code="000001.SZ", qty=200, cost_price_micro=10_000_000, sellable_date="20260811"),
        Lot(lot_id="L2", ts_code="000001.SZ", qty=100, cost_price_micro=9_500_000, sellable_date="20260811"),
    ]
    result = consume_fifo_lots(lots, 300, sell_price_micro=11_000_000)
    # (11-10)*200 + (11-9.5)*100 = 200 + 150 = 350 元 = 35000 分
    assert result.realized_pnl_fen == 35_000
