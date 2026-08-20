"""alpha 衰减监控（D 阶段）：IC 序列、ICIR、半衰期。

- IC：因子值与前瞻收益的秩相关（Spearman），逐期计算。
- 半衰期：IC 自相关衰减至 0.5 的期数（指数拟合或线性插值）。
- 样本不足 → INSUFFICIENT（不伪造 alpha）。
"""
from __future__ import annotations

from typing import Any

import numpy as np

MIN_IC_PERIODS = 20


class AlphaDecayError(ValueError):
    """alpha 衰减输入非法（fail-closed）。"""


def ic_series(
    factor_by_date: dict[str, float],
    forward_returns_by_date: dict[str, float],
) -> dict[str, float]:
    """逐期 IC（Spearman 秩相关）；样本不足期返回空。"""
    dates = sorted(set(factor_by_date) & set(forward_returns_by_date))
    out: dict[str, float] = {}
    for d in dates:
        out[d] = float(forward_returns_by_date[d])  # 占位：单标的无截面
    # 单标的口径：用时间序列 IC（因子值序列与前瞻收益序列的相关）
    f = np.array([factor_by_date[d] for d in dates], dtype=float)
    r = np.array([forward_returns_by_date[d] for d in dates], dtype=float)
    if len(dates) < MIN_IC_PERIODS:
        return {}
    if np.isnan(f).any() or np.isnan(r).any() or np.std(f) == 0 or np.std(r) == 0:
        return {}
    # 滚动 20 期秩相关
    from scipy.stats import rankdata

    for i in range(MIN_IC_PERIODS, len(dates) + 1):
        fw = f[i - MIN_IC_PERIODS:i]
        rw = r[i - MIN_IC_PERIODS:i]
        ic = float(np.corrcoef(rankdata(fw), rankdata(rw))[0, 1])
        out[dates[i - 1]] = round(ic, 6)
    return out


def ic_half_life(ic_values: list[float]) -> float | None:
    """IC 自相关半衰期：ACF 衰减到 0.5 的滞后阶数（线性插值）。"""
    if len(ic_values) < MIN_IC_PERIODS:
        return None
    arr = np.asarray(ic_values, dtype=float)
    arr = arr - arr.mean()
    if np.std(arr) == 0:
        return None
    # 自相关
    acf = []
    for lag in range(1, min(10, len(arr) // 2) + 1):
        acf.append(float(np.corrcoef(arr[:-lag], arr[lag:])[0, 1]) if len(arr) > lag else 0.0)
    for lag, value in enumerate(acf, start=1):
        if value <= 0.5:
            # 线性插值半衰期
            prev = acf[lag - 2] if lag >= 2 else 1.0
            if prev <= 0.5:
                return float(lag - 1)
            return float(lag - 1 + (prev - 0.5) / max(prev - value, 1e-9))
    return None  # 未衰减到 0.5


def alpha_decay_monitor(ic_values: list[float]) -> dict[str, Any]:
    """监控摘要：mean_ic / ICIR / half_life / status。"""
    if len(ic_values) < MIN_IC_PERIODS:
        return {"status": "INSUFFICIENT", "reason": f"IC 期数不足（{len(ic_values)} < {MIN_IC_PERIODS}）"}
    arr = np.asarray(ic_values, dtype=float)
    if np.isnan(arr).any():
        return {"status": "INSUFFICIENT", "reason": "IC 含 NaN"}
    mean_ic = float(arr.mean())
    std_ic = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    icir = mean_ic / std_ic if std_ic > 0 else None
    half_life = ic_half_life(ic_values)
    return {
        "status": "OK",
        "mean_ic": round(mean_ic, 6),
        "icir": round(icir, 4) if icir is not None else None,
        "half_life_periods": half_life,
        "n_periods": len(arr),
        "note": (
            "alpha 有效性需 mean_ic>0 且半衰期足够长；短半衰期因子仅适合高频换手"
            if half_life is not None and half_life < 10 else
            "alpha 半衰期观察中（≥10 期才可称为稳定）"
            if half_life is not None else "半衰期未衰减到 0.5（alpha 持久性良好或样本不足）"
        ),
    }
