"""P3.3 测试：成本压力（1×/2×/3×）+ 容量模型。"""
from __future__ import annotations

import pytest

from ab_screener.research.capacity import (
    CapacityError,
    account_scale_scenarios,
    daily_capacity_yuan,
    expected_exit_days,
)
from ab_screener.research.cost_stress import (
    CostStressError,
    net_oos_positive_at_2x,
    stress_costs,
)


def test_stress_costs_multipliers():
    """毛收益、费率 10bp（双边 20bp）→ 1×/2×/3× 净收益逐项复算。"""
    gross = [0.010, 0.005, 0.001]
    result = stress_costs(gross, cost_rate=0.001, multipliers=(1.0, 2.0, 3.0))
    # 1×：减 0.002/笔 → [0.008, 0.003, -0.001]，均值 0.010/3
    assert result["results"]["1x"]["net_mean"] == pytest.approx(0.010 / 3, abs=1e-6)
    # 2×：减 0.004/笔 → 总和 0.004
    assert result["results"]["2x"]["net_total"] == pytest.approx(0.004, abs=1e-6)
    # 3×：减 0.006/笔 → 总和 -0.002
    assert result["results"]["3x"]["net_total"] == pytest.approx(-0.002, abs=1e-6)
    assert net_oos_positive_at_2x(result) is True


def test_stress_costs_2x_negative():
    result = stress_costs([0.001, 0.001, 0.001], cost_rate=0.001)
    assert net_oos_positive_at_2x(result) is False


def test_stress_fail_closed():
    with pytest.raises(CostStressError, match="为空"):
        stress_costs([], 0.001)
    with pytest.raises(CostStressError, match="NaN"):
        stress_costs([0.01, float("nan")], 0.001)
    with pytest.raises(CostStressError, match="费率"):
        stress_costs([0.01], -0.001)


def test_capacity_basic():
    # ADV20=1 亿元，5% 参与率 → 500 万元/日
    cap = daily_capacity_yuan(100_000_000, participation_bps=500)
    assert cap == pytest.approx(5_000_000, abs=1.0)
    # 持仓 1000 万 → 2 天退出
    assert expected_exit_days(10_000_000, 100_000_000, 500) == pytest.approx(2.0, abs=1e-6)


def test_capacity_cap_and_fail_closed():
    # 100% 参与率被 cap_pct_of_adv 封顶在 5%
    cap = daily_capacity_yuan(100_000_000, participation_bps=10_000)
    assert cap == pytest.approx(5_000_000, abs=1.0)
    with pytest.raises(CapacityError, match="ADV20"):
        daily_capacity_yuan(0, 500)
    with pytest.raises(CapacityError, match="参与率"):
        daily_capacity_yuan(100_000_000, 20_000)


def test_account_scale_scenarios():
    scenarios = account_scale_scenarios(100_000_000, [0.5, 1.0], participation_bps=500)
    assert len(scenarios) == 2
    assert scenarios[0]["days_to_exit"] == pytest.approx(0.5, abs=1e-6)
    assert scenarios[1]["capacity_ok"] is True
