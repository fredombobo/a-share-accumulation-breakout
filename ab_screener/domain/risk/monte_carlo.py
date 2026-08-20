"""蒙特卡洛 VaR/CVaR（C 阶段）：确定性种子、几何布朗运动模拟。

- 参数化（μ, σ）从收益序列估计；几何布朗运动路径 → 期末损益分布。
- 确定性种子保证可复现（审计要求）。
- 样本不足/零方差/NaN → INSUFFICIENT（不伪造）。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

MIN_SAMPLES = 30


class MonteCarloError(ValueError):
    """蒙特卡洛输入非法（fail-closed）。"""


def monte_carlo_var_cvar(
    returns: np.ndarray,
    *,
    n_sims: int = 20_000,
    horizon: int = 1,
    confidence: float = 0.95,
    seed: int = 42,
) -> dict[str, Any]:
    """单资产收益序列 → 蒙特卡洛 VaR/CVaR（损失为正口径）。

    returns: 每期收益（小数）；horizon 为持有期数（√horizon 缩放波动）。
    """
    arr = np.asarray(returns, dtype=float).reshape(-1)
    if arr.size < MIN_SAMPLES:
        return {"status": "INSUFFICIENT", "reason": f"样本不足（{arr.size} < {MIN_SAMPLES}）"}
    if np.isnan(arr).any() or np.isinf(arr).any():
        return {"status": "INSUFFICIENT", "reason": "收益含 NaN/Inf"}
    if n_sims < 100:
        raise MonteCarloError("模拟次数必须 ≥100")
    if not 0.0 < confidence < 1.0:
        raise MonteCarloError("置信水平必须在 (0,1)")
    if horizon < 1:
        raise MonteCarloError("持有期必须 ≥1")

    mu = float(arr.mean())
    sigma = float(arr.std(ddof=1))
    if sigma <= 0:
        return {"status": "INSUFFICIENT", "reason": "收益零方差"}

    rng = np.random.default_rng(seed)
    scale = sigma * math.sqrt(horizon)
    shocks = rng.normal(0.0, 1.0, size=n_sims)
    end_returns = mu * horizon + scale * shocks  # 简化为参数化正态（GBM 对数近似的收益率口径）
    losses = -end_returns  # 损失为正
    var = float(np.percentile(losses, confidence * 100, method="linear"))
    tail = losses[losses >= var]
    cvar = float(tail.mean()) if tail.size else var
    return {
        "status": "OK",
        "var95": round(var, 6),
        "cvar95": round(cvar, 6),
        "n_sims": n_sims,
        "horizon": horizon,
        "confidence": confidence,
        "seed": seed,
        "mu_period": round(mu, 6),
        "sigma_period": round(sigma, 6),
    }


def portfolio_monte_carlo_var_cvar(
    weights: np.ndarray,
    covariance: np.ndarray,
    *,
    n_sims: int = 20_000,
    horizon: int = 1,
    confidence: float = 0.95,
    seed: int = 42,
) -> dict[str, Any]:
    """组合（权重×协方差）蒙特卡洛 VaR/CVaR：多元正态模拟。"""
    w = np.asarray(weights, dtype=float)
    cov = np.asarray(covariance, dtype=float)
    if w.ndim != 1 or cov.ndim != 2 or cov.shape[0] != cov.shape[1]:
        raise MonteCarloError("权重/协方差维度非法")
    if len(w) != cov.shape[0]:
        raise MonteCarloError("权重数与协方差维度不一致")
    if np.isnan(cov).any() or np.isinf(cov).any():
        raise MonteCarloError("协方差含 NaN/Inf")
    eigvals = np.linalg.eigvalsh(cov)
    if float(eigvals.min()) < -1e-8:
        raise MonteCarloError("协方差非半正定")

    rng = np.random.default_rng(seed)
    shocks = rng.multivariate_normal(np.zeros(len(w)), cov * horizon, size=n_sims)
    port_returns = shocks @ w
    losses = -port_returns
    var = float(np.percentile(losses, confidence * 100, method="linear"))
    tail = losses[losses >= var]
    cvar = float(tail.mean()) if tail.size else var
    return {
        "status": "OK",
        "var95": round(var, 6),
        "cvar95": round(cvar, 6),
        "n_sims": n_sims,
        "horizon": horizon,
        "confidence": confidence,
        "seed": seed,
    }
