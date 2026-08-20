"""正式统计：MinTRL 最小业绩记录长度（P3.2）。

公式（Bailey & López de Prado, "The Minimum Track Record Length"）：
    n* = 1 + [1 - γ₃·SR̂ + ((γ₄-1)/4)·SR̂²] · (z_α / SR̂)²
z_α 为置信水平 α 的标准正态分位数；SR̂ 为**每期**（非年化）Sharpe。
NaN/非有限输入 → ValueError（fail-closed）。
"""
from __future__ import annotations

import math

from scipy import stats


def min_track_record_length(
    sharpe: float,
    skew: float,
    kurtosis: float,
    *,
    confidence: float = 0.95,
) -> float:
    """达到给定置信水平所需的最小观测期数。"""
    if not all(math.isfinite(v) for v in (sharpe, skew, kurtosis)):
        raise ValueError("MinTRL 输入必须为有限值（拒绝 NaN/Inf）")
    if sharpe <= 0:
        raise ValueError("Sharpe 必须为正（非正收益无 MinTRL 意义）")
    if not 0.0 < confidence < 1.0:
        raise ValueError("置信水平必须在 (0,1)")
    z = stats.norm.ppf(confidence)
    inner = 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe
    if inner <= 0:
        raise ValueError(f"MinTRL 内项非正（偏度/峰度组合非法）: {inner}")
    return 1.0 + inner * (z / sharpe) ** 2


def min_track_record_summary(**kwargs) -> dict:
    return {
        "min_track_record_length": round(
            min_track_record_length(
                kwargs["sharpe"], kwargs["skew"], kwargs["kurtosis"],
                confidence=kwargs.get("confidence", 0.95),
            ),
            2,
        ),
        "confidence": kwargs.get("confidence", 0.95),
        "sharpe_period": kwargs["sharpe"],
        "skew": kwargs["skew"],
        "kurtosis": kwargs["kurtosis"],
    }
