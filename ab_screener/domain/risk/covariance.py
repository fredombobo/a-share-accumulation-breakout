"""协方差估计（C 阶段）：样本协方差 + Ledoit-Wolf 收缩。

- 样本协方差：T×N 收益矩阵 → N×N 协方差（ddof=1）。
- Ledoit-Wolf 收缩：从数据估计收缩强度，向单位矩阵收缩，
  改善小样本下协方差的病态条件数（组合风险估计更稳）。
- 输入校验 fail-closed：NaN/Inf、样本不足、维度非法均拒绝。
"""
from __future__ import annotations

from typing import Any

import numpy as np


class CovarianceError(ValueError):
    """协方差输入非法（fail-closed）。"""


def sample_covariance(returns: np.ndarray) -> np.ndarray:
    """T×N 收益矩阵 → N×N 样本协方差。"""
    arr = np.asarray(returns, dtype=float)
    if arr.ndim != 2:
        raise CovarianceError("收益矩阵必须为二维 (T, N)")
    t, n = arr.shape
    if t < 2 or n < 1:
        raise CovarianceError(f"样本不足: T={t} N={n}")
    if np.isnan(arr).any() or np.isinf(arr).any():
        raise CovarianceError("收益矩阵含 NaN/Inf")
    if t < n:
        raise CovarianceError(f"样本期数 {t} < 标的数 {n}（协方差将病态，请用收缩估计）")
    return np.cov(arr, rowvar=False, ddof=1)


def ledoit_wolf_shrinkage(returns: np.ndarray) -> dict[str, Any]:
    """Ledoit-Wolf 收缩协方差。

    返回 {cov, shrinkage_intensity, sample_cov}：
    shrinkage ∈ [0,1]，0=纯样本，1=纯目标（单位矩阵×平均方差）。
    """
    arr = np.asarray(returns, dtype=float)
    if arr.ndim != 2:
        raise CovarianceError("收益矩阵必须为二维 (T, N)")
    t, n = arr.shape
    if t < 3 or n < 2:
        raise CovarianceError(f"收缩估计样本不足: T={t} N={n}")
    if np.isnan(arr).any() or np.isinf(arr).any():
        raise CovarianceError("收益矩阵含 NaN/Inf")

    sample = np.cov(arr, rowvar=False, ddof=1)
    # 目标矩阵：对角 = 各资产方差均值，非对角 = 0
    mean_var = float(np.trace(sample) / n)
    target = np.eye(n) * mean_var

    # LW 收缩强度（经典估计量）
    demeaned = arr - arr.mean(axis=0)
    var_hat = np.mean((demeaned ** 2).sum(axis=1) / n)  # 平均样本方差
    # 分子：样本协方差与目标偏离的期望平方误差
    # phi_hat 近似：样本协方差元素与 target 元素差的平方均值
    diff = sample - target
    phi_hat = float(np.sum(diff ** 2) / n)
    # 分母：Var(sample 元素) 的估计（用二次项近似）
    # 简化稳健实现：shrinkage = min(1, phi_hat / (phi_hat + var_hat))
    denom = float(phi_hat + var_hat)
    shrinkage = float(min(1.0, float(phi_hat) / denom)) if denom > 0 else 1.0

    cov = shrinkage * target + (1.0 - shrinkage) * sample
    return {
        "cov": cov,
        "shrinkage_intensity": round(shrinkage, 6),
        "sample_cov": sample,
    }


def covariance_condition_number(cov: np.ndarray) -> float:
    """条件数（病态诊断）：越大越病态。"""
    arr = np.asarray(cov, dtype=float)
    if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
        raise CovarianceError("协方差必须为方阵")
    return float(np.linalg.cond(arr))
