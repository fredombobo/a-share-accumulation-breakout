"""P2.2 测试：零成交/部分成交/DAY 余量过期语义。"""
from __future__ import annotations

import pytest

from ab_screener.domain.execution.fill_model import FillRequest, compute_fill
from ab_screener.domain.execution.models import MoneyError, Quote
from ab_screener.domain.execution.order_semantics import (
    expire_day_remainder,
    suspension_is_not_fill,
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


def test_zero_fill_when_no_open_suspension_volume():
    """无 open/停牌/vol=0/无报价：零成交。"""
    cases = [
        _quote(open_micro=0, close_micro=10_200_000),
        _quote(open_micro=0, close_micro=0),
        _quote(vol=0),
    ]
    for q in cases:
        fill = compute_fill(q, FillRequest(ts_code="x", side="BUY", trade_date="d",
                                           input_hash="h"))
        assert fill.filled is False
        assert suspension_is_not_fill(q) is True


def test_day_remainder_expires_after_partial_fill():
    """DAY：实际成交后余量过期（不自动顺延）。"""
    assert expire_day_remainder(requested_qty=1000, filled_qty=400) == 600
    assert expire_day_remainder(requested_qty=1000, filled_qty=1000) == 0
    with pytest.raises(MoneyError, match="重复成交"):
        expire_day_remainder(requested_qty=100, filled_qty=200)


def test_partial_fill_qty_respects_participation():
    """参与率 5% 限制最大成交量（部分成交场景）。"""
    q = _quote(vol=10_000)  # 1 万股
    fill = compute_fill(q, FillRequest(ts_code="x", side="BUY", trade_date="d",
                                       input_hash="h", cash_available_fen=10_000_000_00))
    assert fill.filled is True
    assert fill.qty == 500  # 5% → 500 股（整手）
    assert fill.max_qty == 500
