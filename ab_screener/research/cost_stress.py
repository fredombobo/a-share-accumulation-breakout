"""成本压力测试（P3.3）：1×/2×/3× 成本下的净收益重算。

输入：逐笔毛收益（小数）与逐笔费用率（如佣金率/其他费率，双边折算为单边占比）；
输出各倍率下的净收益序列与摘要（净均值/PF/胜率）。
"""
from __future__ import annotations

from typing import Any

import numpy as np


class CostStressError(ValueError):
    """成本压力输入非法（fail-closed）。"""


def stress_costs(
    gross_returns: list[float],
    cost_rate: float,
    multipliers: tuple[float, ...] = (1.0, 2.0, 3.0),
) -> dict[str, Any]:
    """gross_returns: 逐笔毛收益（小数）；cost_rate: 单边费率（小数，双边已折算）。"""
    arr = np.asarray(gross_returns, dtype=float).reshape(-1)
    if arr.size == 0:
        raise CostStressError("收益序列为空")
    if np.isnan(arr).any() or np.isinf(arr).any():
        raise CostStressError("收益序列含 NaN/Inf")
    if cost_rate < 0:
        raise CostStressError("费率不能为负")
    out: dict[str, Any] = {}
    for mult in multipliers:
        net = arr - 2.0 * cost_rate * mult  # 双边成本
        positives = net[net > 0]
        negatives = net[net < 0]
        pf = (float(positives.sum()) / abs(float(negatives.sum()))
              if positives.size and negatives.size else None)
        out[f"{mult:g}x"] = {
            "net_mean": round(float(net.mean()), 6),
            "net_win_rate": round(float((net > 0).mean()), 4),
            "net_profit_factor": round(pf, 3) if pf is not None else None,
            "net_total": round(float(net.sum()), 6),
        }
    return {"cost_rate": cost_rate, "multipliers": list(multipliers), "results": out}


def net_oos_positive_at_2x(results: dict[str, Any]) -> bool:
    """2× 成本下净 OOS 为正（ROBUST_PERSONAL_V2 门槛之一）。"""
    entry = results.get("results", {}).get("2x")
    return bool(entry and entry["net_total"] > 0)
