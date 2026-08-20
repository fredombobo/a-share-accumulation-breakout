"""P2.2 测试：时间规则（收盘信号护栏/滑点钳制/涨跌停零成交）。"""
from __future__ import annotations

import pytest

from ab_screener.domain.execution.market_rules import (
    can_trade,
    limit_prices_micro,
    participation_max_qty,
)
from ab_screener.domain.execution.models import MoneyError, Quote
from ab_screener.domain.execution.order_semantics import (
    SignalTiming,
    assert_no_same_close_fill,
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


def test_close_signal_same_close_fill_rejected():
    """同日收盘信号按同一收盘成交 → 必须失败。"""
    with pytest.raises(MoneyError, match="时间护栏"):
        assert_no_same_close_fill(
            SignalTiming(signal_date="20260810", signal_close_micro=10_200_000,
                         fill_date="20260810", fill_time="CLOSE")
        )
    # 次日开盘成交合法
    assert_no_same_close_fill(
        SignalTiming(signal_date="20260810", signal_close_micro=10_200_000,
                     fill_date="20260811", fill_time="OPEN")
    )


def test_limit_prices_and_one_side_blocks():
    q = _quote(pre_close_micro=10_000_000)
    up, down = limit_prices_micro(q)
    assert up == 11_000_000 and down == 9_000_000  # ±10%，tick 0.01
    one_up = _quote(open_micro=up, high_micro=up, low_micro=up, pre_close_micro=10_000_000)
    ok, reason = can_trade(one_up, "BUY")
    assert not ok and reason == "LIMIT_UP_ONE_SIDE"


def test_participation_versioned_bps():
    # 版本化参与率：500bps=5%、1000bps=10%、10000bps=100%
    assert participation_max_qty(1_000_000, 500) == 50_000
    assert participation_max_qty(1_000_000, 1000) == 100_000
    assert participation_max_qty(1_000_000, 10_000) == 1_000_000
