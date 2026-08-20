"""
全市场扫描预筛（加速）
======================
在逐只 detect 之前，用最近交易日量能/涨跌粗筛，砍掉明显不可能突破的票。
upgrade system：向量化 groupby，避免纯 Python 逐票循环。
"""
from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def volume_breakout_candidates(
    daily: pd.DataFrame,
    codes: Iterable[str],
    *,
    lookback: int = 25,
    vol_ratio_min: float = 1.15,
    near_high_pct: float = 0.08,
) -> set[str]:
    """返回「近 lookback 日有放量 或 接近区间高点」的代码集合。"""
    code_set = {str(c) for c in codes}
    if daily is None or daily.empty or not code_set:
        return code_set

    need_cols = {"ts_code", "trade_date", "close", "high"}
    if not need_cols.issubset(set(daily.columns)):
        return set()
    vol_col = "vol" if "vol" in daily.columns else ("volume" if "volume" in daily.columns else None)
    if vol_col is None:
        return set()

    d = daily.loc[daily["ts_code"].isin(code_set), ["ts_code", "trade_date", "close", "high", vol_col]].copy()
    d = d.rename(columns={vol_col: "vol"})
    d["vol"] = pd.to_numeric(d["vol"], errors="coerce").fillna(0.0)
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d["high"] = pd.to_numeric(d["high"], errors="coerce")
    d = d.dropna(subset=["close"])
    d = d.sort_values(["ts_code", "trade_date"])
    d = d.groupby("ts_code", sort=False, group_keys=False).tail(lookback)

    # 样本不足剔除
    cnt = d.groupby("ts_code")["close"].transform("size")
    d = d.loc[cnt >= 10]
    if d.empty:
        return set()

    g = d.groupby("ts_code", sort=False)
    last = g.tail(1).set_index("ts_code")
    sum_vol = g["vol"].sum()
    n = g["vol"].size()
    last_vol = last["vol"]
    avg_prev = (sum_vol - last_vol) / (n - 1).clip(lower=1)
    cond_vol = (avg_prev > 0) & (last_vol >= vol_ratio_min * avg_prev)

    # 近 5 日最大量 vs 更早均量
    d = d.copy()
    d["_rev"] = g.cumcount(ascending=False)
    max5 = d.loc[d["_rev"] < 5].groupby("ts_code")["vol"].max()
    early_avg = d.loc[d["_rev"] >= 5].groupby("ts_code")["vol"].mean()
    cond_r5 = (early_avg.reindex(last.index) > 0) & (
        max5.reindex(last.index) >= vol_ratio_min * early_avg.reindex(last.index)
    )

    win_hi = g["high"].max()
    cond_hi = (win_hi > 0) & (last["close"] >= win_hi.reindex(last.index) * (1.0 - near_high_pct))

    mask = cond_vol.fillna(False) | cond_r5.fillna(False) | cond_hi.fillna(False)
    return {str(x) for x in last.index[mask]}
