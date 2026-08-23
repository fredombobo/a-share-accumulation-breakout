"""V2R-X dual-run 集成：同一冻结行情/规则/订单，legacy 与 v2 核心逐项一致。"""
from __future__ import annotations

from typing import Any

import pytest

from ab_screener.domain.execution.dual_run import FrozenOrder, compare_round_trip
from paper_trading.rules import InstrumentRule


def _rule() -> InstrumentRule:
    return InstrumentRule(ts_code="000001.SZ", inst_type="STOCK")


def _bar(**over: Any) -> dict[str, Any]:
    bar: dict[str, Any] = {
        "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
        "vol": 50_000, "amount": 50_000_000.0, "pre_close": 9.9,
    }
    bar.update(over)
    return bar


def _frozen(side: str = "BUY", qty: int = 1000, bar: dict[str, Any] | None = None,
            position_qty: int = 0) -> FrozenOrder:
    return FrozenOrder(
        bar=bar, side=side, qty=qty, rule=_rule(),
        ts_code="000001.SZ", trade_date="20260807", input_hash="h",
        cash_available_fen=1_000_000_00, position_qty=position_qty,
    )


def test_dual_run_has_zero_fen_difference_buy() -> None:
    """正常买入：成交数量/现金/费用逐项一致（0 分差）。"""
    result = compare_round_trip(_frozen(side="BUY", qty=1000, bar=_bar()))
    assert result["quantity_diff"] == 0, result["diffs"]
    assert result["cash_diff_fen"] == 0, result["diffs"]
    assert result["fee_diff_fen"] == 0, result["diffs"]
    assert result["parity"] is True, result["diffs"]


def test_dual_run_has_zero_fen_difference_sell() -> None:
    """正常卖出（含印花税）：逐项一致。"""
    result = compare_round_trip(_frozen(side="SELL", qty=1000, bar=_bar(), position_qty=1000))
    assert result["quantity_diff"] == 0, result["diffs"]
    assert result["cash_diff_fen"] == 0, result["diffs"]
    assert result["fee_diff_fen"] == 0, result["diffs"]
    assert result["parity"] is True, result["diffs"]


def test_dual_run_stop_bar_returns_no_quote() -> None:
    """停牌（bar=None）：两入口都零成交。"""
    result = compare_round_trip(_frozen(side="BUY", qty=1000, bar=None))
    assert result["quantity_diff"] == 0
    assert result["cash_diff_fen"] == 0
    assert result["v2_reason"] == "NO_QUOTE"


def test_dual_run_zero_volume() -> None:
    """零成交量：两入口都零成交，数量/现金/费用一致。"""
    result = compare_round_trip(_frozen(side="BUY", qty=1000, bar=_bar(vol=0)))
    assert result["quantity_diff"] == 0
    assert result["cash_diff_fen"] == 0
    assert result["fee_diff_fen"] == 0


def test_dual_run_one_word_limit_up_buy() -> None:
    """一字涨停买入：零成交，两入口一致。"""
    bar = _bar(open=10.0, high=10.0, low=10.0, close=10.0, pre_close=9.09)
    result = compare_round_trip(_frozen(side="BUY", qty=1000, bar=bar))
    assert result["quantity_diff"] == 0
    assert result["fee_diff_fen"] == 0


def test_dual_run_one_word_limit_down_sell() -> None:
    """一字跌停卖出：零成交，两入口一致。"""
    bar = _bar(open=9.0, high=9.0, low=9.0, close=9.0, pre_close=10.0)
    result = compare_round_trip(_frozen(side="SELL", qty=1000, bar=bar, position_qty=1000))
    assert result["quantity_diff"] == 0
    assert result["fee_diff_fen"] == 0
