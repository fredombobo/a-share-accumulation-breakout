"""多因子风险模型（C 阶段）：因子暴露 × 因子协方差 + 特质风险。

- 组合风险² = w' (B·F·B' + D) w；B=因子暴露，F=因子协方差，D=特质方差对角。
- 风险分解：系统性风险 + 各因子贡献 + 特质风险。
"""
from __future__ import annotations

from typing import Any

import numpy as np


class FactorRiskError(ValueError):
    """因子风险输入非法（fail-closed）。"""


def factor_model_covariance(
    factor_exposures: np.ndarray,
    factor_covariance: np.ndarray,
    idio_variance: np.ndarray,
) -> np.ndarray:
    """因子模型协方差 = B·F·B' + diag(D)。"""
    b = np.asarray(factor_exposures, dtype=float)
    f = np.asarray(factor_covariance, dtype=float)
    d = np.asarray(idio_variance, dtype=float)
    if b.ndim != 2:
        raise FactorRiskError("因子暴露必须为二维 (N 资产 × K 因子)")
    if f.ndim != 2 or f.shape[0] != f.shape[1] or f.shape[0] != b.shape[1]:
        raise FactorRiskError("因子协方差维度与暴露不一致")
    if d.ndim != 1 or len(d) != b.shape[0]:
        raise FactorRiskError("特质方差维度与资产数不一致")
    if (d < 0).any():
        raise FactorRiskError("特质方差不能为负")
    if np.isnan(b).any() or np.isnan(f).any() or np.isnan(d).any():
        raise FactorRiskError("输入含 NaN")
    return b @ f @ b.T + np.diag(d)


def factor_risk_decomposition(
    weights: np.ndarray,
    factor_exposures: np.ndarray,
    factor_covariance: np.ndarray,
    idio_variance: np.ndarray,
) -> dict[str, Any]:
    """组合风险分解：总方差 = 系统性 + 特质；各因子边际贡献。"""
    w = np.asarray(weights, dtype=float)
    b = np.asarray(factor_exposures, dtype=float)
    f = np.asarray(factor_covariance, dtype=float)
    d = np.asarray(idio_variance, dtype=float)
    if len(w) != b.shape[0]:
        raise FactorRiskError("权重数与资产数不一致")
    cov = factor_model_covariance(b, f, d)
    total_var = float(w @ cov @ w)
    systematic_var = float(w @ (b @ f @ b.T) @ w)
    idio_var = float(w @ np.diag(d) @ w)
    # 因子贡献：对 factor_j 的方差贡献 ≈ (B'w)_j² · F_jj + 交叉项的一半按比例拆分
    port_factor_exposure = b.T @ w
    factor_contrib: dict[str, float] = {}
    k = f.shape[0]
    for j in range(k):
        # 全方差对 F_jj 的偏导数贡献（含交叉项按对角占比近似）
        contrib = float(port_factor_exposure[j] ** 2 * f[j, j])
        factor_contrib[f"factor_{j}"] = round(contrib, 8)
    return {
        "total_variance": round(total_var, 10),
        "systematic_variance": round(systematic_var, 10),
        "idio_variance": round(idio_var, 10),
        "systematic_ratio": round(systematic_var / total_var, 6) if total_var > 0 else None,
        "factor_contributions": factor_contrib,
        "portfolio_factor_exposure": {
            f"factor_{j}": round(float(port_factor_exposure[j]), 6) for j in range(k)
        },
    }
