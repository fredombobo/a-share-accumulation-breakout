"""P5.2 风险指标测试：TWR/波动/Sharpe/回撤/VaR/CVaR/权重合计/证据不足。"""
from __future__ import annotations

import math

import pytest

from ab_screener.domain.risk.analytics import (
    RiskMetricsError,
    concentration,
    liquidity_days,
    portfolio_metrics,
    twr,
    weights_sum_to_one,
)


def _flat_curve(n: int = 252, step: float = 0.001) -> list[float]:
    """确定性权益曲线（每日 +0.1% + 微小确定波动，避免零方差）。"""
    return [1.0 * (1.0 + step) ** i + (i % 7) * 0.00005 for i in range(n)]


def test_twr_hand_calc():
    curve = _flat_curve(252, 0.001)
    returns = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    expected = math.prod(1.0 + r for r in returns) - 1.0
    assert twr(curve) == pytest.approx(expected, abs=1e-8)


def test_metrics_ok_and_insufficient():
    metrics = portfolio_metrics(_flat_curve())
    assert metrics["status"] == "OK"
    assert metrics["max_drawdown"] == 0.0        # 单调上升
    assert metrics["var95"] >= 0.0
    assert metrics["cvar95"] >= metrics["var95"]
    # 样本不足 → INSUFFICIENT 不返回 0
    small = portfolio_metrics([1.0, 1.01, 1.02])
    assert small["status"] == "INSUFFICIENT"
    # NaN → INSUFFICIENT
    bad = portfolio_metrics([1.0, float("nan"), 1.1])
    assert bad["status"] == "INSUFFICIENT"


def test_sharpe_sign():
    """正漂移曲线 Sharpe 为正；负漂移为负。"""
    up = portfolio_metrics(_flat_curve(252, 0.001))
    down = portfolio_metrics(_flat_curve(252, -0.001))
    assert up["sharpe_annual"] > 0
    assert down["sharpe_annual"] < 0


def test_weights_sum_to_one():
    assert weights_sum_to_one(0.3, [0.4, 0.2, 0.1]) == pytest.approx(1.0, abs=1e-8)


def test_concentration():
    c = concentration([0.5, 0.3, 0.2])
    assert c["max_weight"] == pytest.approx(0.5)
    assert c["hhi"] == pytest.approx(0.38, abs=1e-6)
    assert concentration([])["status"] == "INSUFFICIENT"


def test_liquidity_days():
    assert liquidity_days(10_000_000, 5_000_000) == pytest.approx(2.0)
    assert liquidity_days(10_000_000, 0) is None  # 证据不足


def test_equity_curve_validation():
    with pytest.raises(RiskMetricsError, match="至少需要 2 个点"):
        twr([1.0])
    with pytest.raises(RiskMetricsError, match="必须为正"):
        twr([1.0, -1.0, 1.1])
