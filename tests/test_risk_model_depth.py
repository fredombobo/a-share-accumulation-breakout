"""C 阶段风险模型测试：协方差/LW 收缩/蒙特卡洛/多因子/极端情景。"""
from __future__ import annotations

import numpy as np
import pytest

from ab_screener.domain.risk.covariance import (
    CovarianceError,
    covariance_condition_number,
    ledoit_wolf_shrinkage,
    sample_covariance,
)
from ab_screener.domain.risk.models import PortfolioState, Position
from ab_screener.domain.risk.monte_carlo import (
    MonteCarloError,
    monte_carlo_var_cvar,
    portfolio_monte_carlo_var_cvar,
)
from ab_screener.domain.risk.multi_factor import (
    FactorRiskError,
    factor_model_covariance,
    factor_risk_decomposition,
)
from ab_screener.domain.risk.stress_library import (
    SCENARIOS,
    apply_extreme_scenario,
    extreme_scenario_report,
)


def _returns(t: int = 300, n: int = 5, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.01, size=(t, n))


def test_sample_covariance_symmetric_positive():
    cov = sample_covariance(_returns())
    assert cov.shape == (5, 5)
    assert np.allclose(cov, cov.T)
    assert np.all(np.diag(cov) > 0)


def test_covariance_fail_closed():
    with pytest.raises(CovarianceError, match="二维"):
        sample_covariance(np.zeros(10))
    with pytest.raises(CovarianceError, match="样本不足"):
        sample_covariance(np.zeros((1, 3)))
    arr = _returns()
    arr[0, 0] = np.nan
    with pytest.raises(CovarianceError, match="NaN"):
        sample_covariance(arr)
    with pytest.raises(CovarianceError, match="病态"):
        sample_covariance(_returns(t=5, n=10))


def test_ledoit_wolf_improves_condition():
    """小样本下 LW 收缩应改善条件数。"""
    small = _returns(t=15, n=8, seed=2)
    result = ledoit_wolf_shrinkage(small)
    sample_cond = covariance_condition_number(result["sample_cov"])
    shrunk_cond = covariance_condition_number(result["cov"])
    assert 0.0 <= result["shrinkage_intensity"] <= 1.0
    assert shrunk_cond <= sample_cond * 1.001 or shrunk_cond < sample_cond
    # 大样本 → 收缩强度趋近 0（接近纯样本）
    large = ledoit_wolf_shrinkage(_returns(t=500, n=5))
    assert large["shrinkage_intensity"] < result["shrinkage_intensity"]


def test_monte_carlo_var_reproducible_and_insufficient():
    r = monte_carlo_var_cvar(_returns(t=300, n=1)[:, 0], seed=7)
    r2 = monte_carlo_var_cvar(_returns(t=300, n=1)[:, 0], seed=7)
    assert r["status"] == "OK"
    assert r["var95"] == r2["var95"]  # 确定性种子可复现
    assert r["cvar95"] >= r["var95"]
    # 样本不足 → INSUFFICIENT
    small = monte_carlo_var_cvar(np.array([0.01, 0.02]), seed=7)
    assert small["status"] == "INSUFFICIENT"
    with pytest.raises(MonteCarloError, match="模拟次数"):
        monte_carlo_var_cvar(_returns(t=100, n=1)[:, 0], n_sims=10)


def test_portfolio_monte_carlo():
    cov = sample_covariance(_returns())
    w = np.ones(5) / 5
    r = portfolio_monte_carlo_var_cvar(w, cov, seed=3)
    assert r["status"] == "OK" and r["cvar95"] >= r["var95"]
    with pytest.raises(MonteCarloError, match="维度"):
        portfolio_monte_carlo_var_cvar(np.ones(3), cov)


def test_factor_model_and_decomposition():
    n_assets, k = 6, 3
    rng = np.random.default_rng(5)
    b = rng.normal(0.5, 0.2, size=(n_assets, k))
    f = np.eye(k) * 0.01
    d = np.full(n_assets, 0.0001)
    cov = factor_model_covariance(b, f, d)
    assert cov.shape == (n_assets, n_assets)
    w = np.ones(n_assets) / n_assets
    decomp = factor_risk_decomposition(w, b, f, d)
    assert decomp["total_variance"] > 0
    assert abs(decomp["systematic_variance"] + decomp["idio_variance"]
               - decomp["total_variance"]) < 1e-9
    assert decomp["systematic_ratio"] is not None
    # 无特质风险时系统性占比 = 1
    decomp2 = factor_risk_decomposition(w, b, f, np.zeros(n_assets))
    assert decomp2["systematic_ratio"] == pytest.approx(1.0, abs=1e-9)
    with pytest.raises(FactorRiskError, match="特质方差不能为负"):
        factor_model_covariance(b, f, np.full(n_assets, -0.001))


def _state() -> PortfolioState:
    return PortfolioState(
        cash_fen=40_000_000, equity_fen=100_000_000,
        positions=(
            Position(ts_code="000001.SZ", qty=1000, latest_close_micro=20_000_000),
            Position(ts_code="600000.SH", qty=2000, latest_close_micro=10_000_000),
        ),
        today="20260818", trade_date="20260818",
    )


def test_extreme_scenarios():
    state = _state()
    assert set(SCENARIOS) == {"GFC_2008", "LEVERAGE_2015", "COVID_2020"}
    gfc = apply_extreme_scenario(state, "GFC_2008")
    assert gfc["index_shock_pct"] == 0.55
    assert gfc["estimated_pnl_fen"] < 0
    assert "参数化模型" in gfc["assumption_note"]
    report = extreme_scenario_report(state)
    assert len(report["scenarios"]) == 3
    with pytest.raises(ValueError, match="未知压力情景"):
        apply_extreme_scenario(state, "NOPE")
