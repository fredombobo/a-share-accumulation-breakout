"""正式统计：Deflated Sharpe Ratio（P3.2）。

DSR 校正选择偏差（试验次数）、偏度与峰度（非正态收益）。
公式（Bailey & López de Prado, "The Deflated Sharpe Ratio"）：
    SR* = √(n-1) · SR̂ / √(1 - γ₃·SR̂ + (γ₄-1)/4·SR̂²)
    DSR = Φ( (SR* - √(n-1)·SR₀) / √(1 - γ₃·SR̂ + (γ₄-1)/4·SR̂²) )
其中 SR₀ = E[max SR] ≈ σ_SR·[(1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e))]，
σ_SR 是全部已记录试验 Sharpe 的横截面标准差，γ = Euler-Mascheroni ≈ 0.5772，
N = 独立试验次数。多试验但缺少 σ_SR 时必须 fail-closed。
NaN/非有限输入 → ValueError（fail-closed，不静默降级）。
"""
from __future__ import annotations

import math
from typing import Any

from scipy import stats

_EULER_GAMMA = 0.5772156649015329


def expected_max_sharpe_null(n_trials: int, *, sharpe_std: float | None = None) -> float:
    """零假设下 N 次独立试验的最大 Sharpe 期望（选择偏差基准）。"""
    if n_trials < 1:
        raise ValueError("试验次数必须 ≥1")
    if n_trials == 1:
        return 0.0
    if sharpe_std is None or not math.isfinite(sharpe_std) or sharpe_std <= 0:
        raise ValueError("多试验 DSR 必须提供正的试验 Sharpe 横截面标准差")
    inv1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    inv2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return sharpe_std * ((1.0 - _EULER_GAMMA) * inv1 + _EULER_GAMMA * inv2)


def deflated_sharpe(
    sharpe: float,
    n_periods: int,
    skew: float,
    kurtosis: float,
    n_trials: int,
    *,
    sr0: float | None = None,
    sharpe_std: float | None = None,
) -> float:
    """DSR：>0.95 表示经过选择偏差与偏峰校正后仍有显著边。"""
    if not all(math.isfinite(v) for v in (sharpe, skew, kurtosis)):
        raise ValueError("DSR 输入必须为有限值（拒绝 NaN/Inf）")
    if n_periods < 2:
        raise ValueError("样本期数必须 ≥2")
    if sr0 is None:
        sr0 = expected_max_sharpe_null(n_trials, sharpe_std=sharpe_std)
    denom_sq = 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe
    if denom_sq <= 0:
        raise ValueError(f"DSR 分母非正（偏度/峰度组合非法）: {denom_sq}")
    denom = math.sqrt(denom_sq)
    dsr = stats.norm.cdf(((sharpe - sr0) * math.sqrt(n_periods - 1)) / denom)
    if not math.isfinite(dsr):
        raise ValueError("DSR 计算失败（输入异常）")
    return float(dsr)


def dsr_summary(**kwargs: Any) -> dict[str, Any]:
    """诊断摘要（含中间量），供报告展示。"""
    sharpe = kwargs["sharpe"]
    n = kwargs["n_periods"]
    skew = kwargs["skew"]
    kurt = kwargs["kurtosis"]
    trials = kwargs["n_trials"]
    sr0 = kwargs.get("sr0")
    if sr0 is None:
        sr0 = expected_max_sharpe_null(trials, sharpe_std=kwargs.get("sharpe_std"))
    return {
        "deflated_sharpe": round(
            deflated_sharpe(sharpe, n, skew, kurt, trials, sr0=sr0),
            6,
        ),
        "sr0_null_max": round(sr0, 6),
        "n_trials": trials,
        "n_periods": n,
        "skew": skew,
        "kurtosis": kurt,
    }
