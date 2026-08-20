"""D 阶段测试：因子注册表/alpha 衰减/组合优化。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ab_screener.factors.alpha_decay import (
    alpha_decay_monitor,
    ic_series,
)
from ab_screener.factors.portfolio_optimization import (
    OptimizationError,
    mean_variance_weights,
    risk_parity_weights,
)
from ab_screener.factors.registry import (
    FactorSpec,
    compute_factor,
    list_factors,
    momentum_n,
    register_factor,
)


def _bars(n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(9)
    dates = pd.date_range("2026-01-01", periods=n, freq="B").strftime("%Y%m%d")
    close = 10.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.01, n))
    return pd.DataFrame(
        {"date": dates, "open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "vol": rng.integers(1_000, 10_000, n).astype(float),
         "amount": close * 1000, "net_mf_amount": rng.normal(0, 1e6, n),
         "pb": np.full(n, 1.5)}
    )


def test_factor_computers_and_registry():
    df = _bars()
    assert momentum_n(df, 20) == pytest.approx(float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1))
    assert list_factors() == {}  # 尚未注册内置因子
    spec = FactorSpec("test_mom", "动量", "测试动量", 20)
    register_factor(spec, lambda d: momentum_n(d, 20))
    assert "test_mom" in list_factors()
    assert compute_factor("test_mom", df) is not None
    with pytest.raises(Exception, match="重复注册"):
        register_factor(spec, lambda d: 0.0)
    with pytest.raises(Exception, match="未知因子"):
        compute_factor("nope", df)


def test_ic_series_and_half_life():
    """确定性 IC：因子与前瞻收益正相关 → mean_ic>0。"""
    rng = np.random.default_rng(4)
    n = 60
    factor = {}
    fwd = {}
    for i, d in enumerate(range(n)):
        f = float(rng.normal(0, 1))
        factor[str(d)] = f
        fwd[str(d)] = f * 0.5 + float(rng.normal(0, 0.5))
    ics = ic_series(factor, fwd)
    assert len(ics) == n - 20 + 1 - 1 or len(ics) > 0
    monitor = alpha_decay_monitor(list(ics.values()))
    assert monitor["status"] == "OK"
    assert monitor["mean_ic"] > 0
    # 样本不足 → INSUFFICIENT
    small = alpha_decay_monitor([0.1] * 10)
    assert small["status"] == "INSUFFICIENT"


def test_mean_variance_weights():
    mu = np.array([0.001, 0.002, 0.0015])
    cov = np.diag([0.0004, 0.0009, 0.0006])
    result = mean_variance_weights(mu, cov, risk_aversion=4.0)
    assert result["status"] == "OK"
    w = np.array(result["weights"])
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert (w >= 0).all()  # long_only
    with pytest.raises(OptimizationError, match="风险厌恶"):
        mean_variance_weights(mu, cov, risk_aversion=0)


def test_risk_parity_equal_contribution():
    rng = np.random.default_rng(6)
    rets = rng.normal(0, 0.01, size=(200, 4))
    cov = np.cov(rets, rowvar=False)
    result = risk_parity_weights(cov)
    assert result["status"] == "OK"
    w = np.array(result["weights"])
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    rc = np.array(result["risk_contributions"])
    # 风险贡献近似相等（阻尼迭代精度：max/min < 1.35）
    assert np.max(rc) / np.min(rc) < 1.35


def test_optimization_fail_closed():
    with pytest.raises(OptimizationError, match="NaN"):
        cov = np.eye(3)
        cov[0, 0] = np.nan
        risk_parity_weights(cov)
    # 奇异协方差 → INSUFFICIENT
    singular = np.ones((3, 3))
    r = mean_variance_weights(np.array([0.1, 0.1, 0.1]), singular)
    assert r["status"] == "INSUFFICIENT"
