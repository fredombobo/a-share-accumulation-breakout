"""
全市场扫描预筛（加速）
======================
在逐只 detect 之前，用最近交易日量能/涨跌粗筛，砍掉明显不可能突破的票。
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


def volume_breakout_candidates(
    daily: pd.DataFrame,
    codes: Iterable[str],
    *,
    lookback: int = 25,
    vol_ratio_min: float = 1.15,
    near_high_pct: float = 0.08,
) -> set[str]:
    """返回「近 lookback 日有放量 或 接近区间高点」的代码集合。

    - vol_ratio_min: 末日量 / 前段均量
    - near_high_pct: 收盘距区间最高价不超过该比例
    """
    code_set = set(codes)
    if daily is None or daily.empty or not code_set:
        return code_set

    d = daily[daily["ts_code"].isin(code_set)].copy()
    if d.empty:
        return set()
    d["vol"] = pd.to_numeric(d.get("vol", d.get("volume")), errors="coerce").fillna(0)
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d["high"] = pd.to_numeric(d["high"], errors="coerce")
    d = d.dropna(subset=["close"])
    d = d.sort_values(["ts_code", "trade_date"])

    keep: set[str] = set()
    for code, g in d.groupby("ts_code"):
        g = g.tail(lookback)
        if len(g) < 10:
            continue
        vols = g["vol"].values
        closes = g["close"].values
        highs = g["high"].values
        last_v = float(vols[-1])
        avg_v = float(vols[:-1].mean()) if len(vols) > 1 else 0.0
        last_c = float(closes[-1])
        hi = float(highs.max())
        vol_ok = avg_v > 0 and last_v >= vol_ratio_min * avg_v
        # 近 5 日任一日放量也算
        if not vol_ok and len(vols) >= 6:
            tail_avg = float(vols[:-5].mean()) if len(vols) > 5 else avg_v
            if tail_avg > 0 and float(vols[-5:].max()) >= vol_ratio_min * tail_avg:
                vol_ok = True
        near_hi = hi > 0 and last_c >= hi * (1.0 - near_high_pct)
        if vol_ok or near_hi:
            keep.add(str(code))
    return keep
