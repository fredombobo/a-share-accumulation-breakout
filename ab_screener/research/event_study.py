"""事件研究基元：前视收益、截面特征、匹配对照、按日聚类 Bootstrap。

本模块是 Phase 1「先证伪」的测量底座：所有函数都是纯函数，不碰数据库、不读
网络。口径固定：

- 入场：信号日**下一交易日开盘**（与 `ENTRY-DEFINITION-V1` 一致）；
- 出场：信号日后第 H 个交易日收盘；
- 复权：用 Tushare `pct_chg`（已按除权除息调整的 prev_close 计算）连乘，
  不自己拼复权因子；
- 对照组：同一天、同行业优先，按 log(市值)/20日波动/20日动量/换手做稳健
  标准化最近邻，事件股自身与 ±窗口内同源事件股一律排除；
- 置信区间：按**事件日聚类**的 block bootstrap（同日事件不独立，不能按笔算 t）。

研究纪律：本模块只产出条件收益差与不确定度，不产出“策略收益”，不生成候选。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

HORIZONS: tuple[int, ...] = (5, 10, 20, 60)
FEATURE_COLUMNS: tuple[str, ...] = ("log_mv", "vol20", "mom20", "turnover")
DEFAULT_WEIGHTS: dict[str, float] = {
    "log_mv": 1.0,
    "vol20": 1.0,
    "mom20": 1.0,
    "turnover": 0.5,
}


def next_open_forward_returns(
    open_: Sequence[float],
    pre_close: Sequence[float],
    pct_chg: Sequence[float],
    *,
    horizons: Iterable[int] = HORIZONS,
) -> dict[int, np.ndarray]:
    """信号日 i → 次日开盘入场 → i+H 收盘出场的收益。

    与生产入场定义一致；`pct_chg` 用于连乘（含除权除息调整）。
    停牌日没有行情行时按“该股自己的下一个可交易日”顺延，持有期以**个股
    交易日**计。结果对尾部不足 H 根的样本给 NaN，绝不前向填充。
    """
    o = np.asarray(open_, dtype=np.float64)
    pc = np.asarray(pre_close, dtype=np.float64)
    chg = np.asarray(pct_chg, dtype=np.float64)
    n = len(o)
    if not (len(pc) == len(chg) == n):
        raise ValueError("open/pre_close/pct_chg 长度必须一致")

    r = np.where(np.isfinite(chg), chg / 100.0, 0.0)
    cprod = np.concatenate([[1.0], np.cumprod(1.0 + r)])

    out: dict[int, np.ndarray] = {}
    for h in horizons:
        if h < 1:
            raise ValueError("horizon 必须 >= 1")
        vals = np.full(n, np.nan)
        count = n - h
        if count > 0:
            i = np.arange(count)
            e = i + 1
            entry = np.where((pc[e] > 0) & (o[e] > 0), o[e] / pc[e], np.nan)
            growth = cprod[i + h + 1] / cprod[e + 1]
            vals[i] = entry * growth - 1.0
        out[h] = vals
    return out


def cross_section_features(
    close: Sequence[float],
    pct_chg: Sequence[float],
    total_mv: Sequence[float],
    turnover: Sequence[float],
    *,
    vol_window: int = 20,
    mom_window: int = 20,
) -> dict[str, np.ndarray]:
    """逐股时序特征；缺失保持 NaN，由匹配阶段显式剔除。"""
    c = pd.Series(np.asarray(close, dtype=np.float64))
    r = pd.Series(np.asarray(pct_chg, dtype=np.float64) / 100.0)
    mv = np.asarray(total_mv, dtype=np.float64)
    turn = np.asarray(turnover, dtype=np.float64)

    vol = r.rolling(vol_window, min_periods=vol_window).std().to_numpy()
    mom = (c / c.shift(mom_window)).to_numpy() - 1.0
    log_mv = np.log(np.where(mv > 0, mv, np.nan))
    return {
        "log_mv": log_mv,
        "vol20": vol,
        "mom20": mom,
        "turnover": turn,
    }


def _robust_scale(series: pd.Series) -> float:
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = float(q3 - q1)
    if iqr > 0:
        return iqr
    std = float(series.std())
    return std if std > 0 else 1.0


def select_controls(
    event_vector: dict[str, float],
    pool: pd.DataFrame,
    *,
    k: int = 5,
    weights: dict[str, float] | None = None,
) -> tuple[list[int], pd.Series]:
    """在 `pool`（已剔除事件股与同源事件股）里选 k 个最近邻。

    返回 (池内位置列表, 到事件的距离序列)。`pool` 的 index 必须是可排序的
    整数位置；特征列缺失即 NaN 的行应在调用前剔除。
    """
    if k < 1:
        raise ValueError("k 必须 >= 1")
    if pool.empty:
        return [], pd.Series(dtype=float)
    w = dict(DEFAULT_WEIGHTS if weights is None else weights)
    cols = [c for c in FEATURE_COLUMNS if c in pool.columns and c in event_vector]
    if not cols:
        raise ValueError("pool 与事件向量没有可用的匹配特征")

    med = pool[cols].median()
    scale = pd.Series({c: _robust_scale(pool[c]) for c in cols})
    z = (pool[cols] - med) / scale
    ev = pd.Series({c: float(event_vector[c]) for c in cols})
    z_ev = (ev - med) / scale
    dist = (z - z_ev).abs().mul(pd.Series({c: w.get(c, 1.0) for c in cols})).sum(axis=1)
    order = list(dist.sort_values(kind="mergesort").index[:k])
    return order, dist.loc[order]


def block_bootstrap_ci(
    date_keys: Sequence[str],
    values: Sequence[float],
    *,
    reps: int = 2000,
    seed: int = 20260914,
    alpha: float = 0.05,
) -> dict[str, float]:
    """按事件日聚类的 Bootstrap 均值置信区间。"""
    keys = np.asarray(list(date_keys), dtype=object)
    vals = np.asarray(list(values), dtype=np.float64)
    if len(keys) != len(vals):
        raise ValueError("date_keys 与 values 长度必须一致")
    mask = np.isfinite(vals)
    keys, vals = keys[mask], vals[mask]
    if len(vals) == 0:
        return {"n": 0, "mean": float("nan"), "lo": float("nan"), "hi": float("nan")}

    frame = pd.DataFrame({"d": keys, "v": vals})
    groups = [g["v"].to_numpy() for _, g in frame.groupby("d", sort=False)]
    rng = np.random.default_rng(seed)
    means = np.empty(reps, dtype=np.float64)
    n_groups = len(groups)
    for b in range(reps):
        pick = rng.integers(0, n_groups, n_groups)
        means[b] = float(np.concatenate([groups[i] for i in pick]).mean())
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "n": int(len(vals)),
        "n_dates": int(n_groups),
        "mean": float(vals.mean()),
        "median": float(np.median(vals)),
        "lo": float(lo),
        "hi": float(hi),
        "positive_rate": float((vals > 0).mean()),
    }


def summarize_diffs(
    date_keys: Sequence[str],
    values: Sequence[float],
    *,
    reps: int = 2000,
    seed: int = 20260914,
) -> dict[str, float]:
    """均值 + 聚类 Bootstrap + 简单描述统计（不返回 t 值，避免伪独立）。"""
    out = block_bootstrap_ci(date_keys, values, reps=reps, seed=seed)
    vals = np.asarray(list(values), dtype=np.float64)
    vals = vals[np.isfinite(vals)]
    if len(vals):
        out["std"] = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
    return out
