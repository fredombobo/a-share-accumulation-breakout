"""组合层优化（D 阶段）：均值-方差 + 风险平价。

- 均值-方差：w = (1/γ)·Σ⁻¹μ 解析解 + 和=1 归一（允许做空约束可选）。
- 风险平价：等风险贡献迭代（对数障碍法简化实现）。
- 协方差奇异/NaN → INSUFFICIENT（不伪造权重）。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np


class OptimizationError(ValueError):
    """组合优化输入非法（fail-closed）。"""


def _validate(cov: np.ndarray, expected_returns: np.ndarray | None = None) -> np.ndarray:
    c = np.asarray(cov, dtype=float)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise OptimizationError("协方差必须为方阵")
    if np.isnan(c).any() or np.isinf(c).any():
        raise OptimizationError("协方差含 NaN/Inf")
    eigvals = np.linalg.eigvalsh(c)
    if float(eigvals.min()) < -1e-8:
        raise OptimizationError("协方差非半正定")
    if expected_returns is not None:
        mu = np.asarray(expected_returns, dtype=float)
        if len(mu) != c.shape[0]:
            raise OptimizationError("预期收益维度与协方差不一致")
        if np.isnan(mu).any():
            raise OptimizationError("预期收益含 NaN")
    return c


def mean_variance_weights(
    expected_returns: np.ndarray,
    covariance: np.ndarray,
    risk_aversion: float = 4.0,
    *,
    long_only: bool = True,
) -> dict[str, Any]:
    """均值-方差最优权重（γ=风险厌恶；long_only 时负权重截断后重归一）。"""
    if risk_aversion <= 0:
        raise OptimizationError("风险厌恶必须为正")
    cov = _validate(covariance, expected_returns)
    mu = np.asarray(expected_returns, dtype=float)
    try:
        inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        return {"status": "INSUFFICIENT", "reason": "协方差奇异，无法求逆（样本不足）"}
    w_unscaled = inv @ mu / risk_aversion
    w = w_unscaled / (w_unscaled.sum() or 1.0)
    if long_only:
        w = np.maximum(w, 0.0)
        total = w.sum()
        if total <= 0:
            return {"status": "INSUFFICIENT", "reason": "长期权重全为负（无可行多头组合）"}
        w = w / total
    return {
        "status": "OK",
        "weights": [round(float(x), 6) for x in w],
        "risk_aversion": risk_aversion,
        "long_only": long_only,
        "portfolio_variance": round(float(w @ cov @ w), 10),
    }


def risk_parity_weights(covariance: np.ndarray, *, max_iter: int = 500, tol: float = 1e-9) -> dict[str, Any]:
    """风险平价权重：各资产边际风险贡献相等（阻尼迭代，收敛更稳）。"""
    cov = _validate(covariance)
    n = cov.shape[0]
    if n < 2:
        raise OptimizationError("风险平价至少需要 2 个资产")
    w = np.full(n, 1.0 / n)
    for _ in range(max_iter):
        var = float(w @ cov @ w)
        if var <= 0:
            return {"status": "INSUFFICIENT", "reason": "组合方差为零"}
        sigma = math.sqrt(var)
        mrc = (cov @ w) / sigma  # 边际风险贡献
        target = float(w @ mrc) / n  # 等贡献目标
        if target <= 0:
            return {"status": "INSUFFICIENT", "reason": "风险贡献非正"}
        # 阻尼更新（0.5 幂）改善收敛稳定性
        ratio = target / np.maximum(mrc, 1e-12)
        w_new = w * np.power(ratio, 0.5)
        w_new = w_new / (w_new.sum() or 1.0)
        if np.max(np.abs(w_new - w)) < tol:
            w = w_new
            break
        w = w_new
    var = float(w @ cov @ w)
    rc = (cov @ w) / (math.sqrt(var) if var > 0 else 1.0)
    return {
        "status": "OK",
        "weights": [round(float(x), 6) for x in w],
        "risk_contributions": [round(float(x), 8) for x in w * rc],
        "portfolio_variance": round(var, 10),
    }
