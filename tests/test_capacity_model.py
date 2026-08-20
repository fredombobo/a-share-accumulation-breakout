"""P3.3 测试：容量模型（ADV20/预计退出天数/账户规模档）。"""
from __future__ import annotations

import pytest

from ab_screener.research.capacity import (
    CapacityError,
    account_scale_scenarios,
    daily_capacity_yuan,
    expected_exit_days,
)


def test_daily_capacity_and_exit_days():
    cap = daily_capacity_yuan(100_000_000, participation_bps=500)
    assert cap == pytest.approx(5_000_000, abs=1.0)
    assert expected_exit_days(20_000_000, 100_000_000) == pytest.approx(4.0, abs=1e-6)


def test_capacity_fail_closed():
    with pytest.raises(CapacityError, match="ADV20"):
        daily_capacity_yuan(0, 500)
    with pytest.raises(CapacityError, match="参与率"):
        daily_capacity_yuan(1e8, 0)


def test_account_scenarios_shape():
    out = account_scale_scenarios(1e8, [0.1, 0.5, 1.0])
    assert len(out) == 3
    assert all("days_to_exit" in s and "capacity_ok" in s for s in out)
    assert out[0]["days_to_exit"] < out[2]["days_to_exit"]
