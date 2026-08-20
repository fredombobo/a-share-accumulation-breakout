"""正式统计：Deflated Sharpe Ratio（P3.2）。

DSR 校正选择偏差（试验次数）、偏度与峰度（非正态收益）。
公式（Bailey & López de Prado, "The Deflated Sharpe Ratio"）：
    SR* = √(n-1) · SR̂ / √(1 - γ₃·SR̂ + (γ₄-1)/4·SR̂²)
    DSR = Φ( (SR* - √(n-1)·SR₀) / √(1 - γ₃·SR̂ + (γ₄-1)/4·SR̂²) )
其中 SR₀ = E[max SR] ≈ ((1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e))) / √(2·ln N)，
γ = Euler-Mascheroni ≈ 0.5772，N = 独立试验次数。
NaN/非有限输入 → ValueError（fail-closed，不静默降级）。
"""
from __future__ import annotations

import math
from typing import Any

from scipy import stats

_EULER_GAMMA = 0.5772156649015329


def expected_max_sharpe_null(n_trials: int) -> float:
    """零假设下 N 次独立试验的最大 Sharpe 期望（选择偏差基准）。"""
    if n_trials < 1:
        raise ValueError("试验次数必须 ≥1")
    if n_trials == 1:
        return 0.0
    inv1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    inv2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return ((1.0 - _EULER_GAMMA) * inv1 + _EULER_GAMMA * inv2) / math.sqrt(2.0 * math.log(n_trials))


def deflated_sharpe(
    sharpe: float,
    n_periods: int,
    skew: float,
    kurtosis: float,
    n_trials: int,
    *,
    sr0: float | None = None,
) -> float:
    """DSR：>0.95 表示经过选择偏差与偏峰校正后仍有显著边。"""
    if not all(math.isfinite(v) for v in (sharpe, skew, kurtosis)):
        raise ValueError("DSR 输入必须为有限值（拒绝 NaN/Inf）")
    if n_periods < 2:
        raise ValueError("样本期数必须 ≥2")
    if sr0 is None:
        sr0 = expected_max_sharpe_null(n_trials)
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
    sr0 = kwargs.get("sr0") or expected_max_sharpe_null(trials)
    return {
        "deflated_sharpe": round(deflated_sharpe(sharpe, n, skew, kurt, trials, sr0=sr0), 6),
        "sr0_null_max": round(sr0, 6),
        "n_trials": trials,
        "n_periods": n,
        "skew": skew,
        "kurtosis": kurt,
    }
