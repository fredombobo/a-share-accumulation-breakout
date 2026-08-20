"""量能类特征（docs §4.1 量能特征第一版）。

  vol_ma_ratio_5_20    5 日均量 / 20 日均量（放量/缩量）
  vol_percentile_60    60 日量能分位（极端量，0~1）
  shrink_days          连续缩量天数（吸筹加分）
  breakout_vol_mult    突破日量 / 箱体均量（突破确认；无 box 时用 20 日均量）
  amount_ratio         成交额 5/20 比（amount 缺失时全 None）
  vp_corr_20           近 20 日价量相关（背离辅助）

入参 df 需含 vol（及可选 amount）；全部只用 t 及以前数据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_volume_features(
    df: pd.DataFrame, box: dict | None = None
) -> pd.DataFrame:
    out = df.copy()
    if "vol" not in out.columns:
        raise ValueError("缺列: vol")

    vol = pd.to_numeric(out["vol"], errors="coerce").fillna(0.0)
    vol5 = vol.rolling(5).mean()
    vol20 = vol.rolling(20).mean()
    out["vol_ma_ratio_5_20"] = vol5 / vol20.replace(0, np.nan)

    # 60 日量能分位（pct_rank，0~1）
    def _pct_rank(s: pd.Series) -> float:
        s = s.dropna()
        if len(s) < 2 or s.iloc[-1] != s.iloc[-1]:  # NaN 保护
            return np.nan
        return float((s <= s.iloc[-1]).mean())

    out["vol_percentile_60"] = vol.rolling(60).apply(
        _pct_rank, raw=False
    )

    # 连续缩量天数：vol 逐日递减计数
    shrink = np.zeros(len(out), dtype=float)
    run = 0
    prev = None
    for i in range(len(out)):
        v = vol.iloc[i]
        if pd.isna(v):
            shrink[i] = 0.0
            prev = None
            continue
        if prev is not None and v < prev:
            run += 1
        else:
            run = 0
        shrink[i] = float(run)
        prev = v
    out["shrink_days"] = shrink

    # 突破量倍数：优先箱体均量，否则 20 日均量
    base = None
    if box and box.get("box_avg_vol"):
        base = float(box["box_avg_vol"])
    if not base:
        base = vol20.iloc[-1] if len(vol20) else np.nan
    out["breakout_vol_mult"] = (vol / base) if (base and base > 0) else np.nan

    # 成交额比（amount 可能缺失 → 全 None）
    if "amount" in out.columns and out["amount"].notna().any():
        amt = pd.to_numeric(out["amount"], errors="coerce")
        amt5 = amt.rolling(5).mean()
        amt20 = amt.rolling(20).mean()
        out["amount_ratio"] = amt5 / amt20.replace(0, np.nan)
    else:
        out["amount_ratio"] = np.nan

    # 20 日价量相关（滚动 corr：close 与 vol）
    c = pd.to_numeric(out["close"], errors="coerce")
    out["vp_corr_20"] = c.rolling(20).corr(vol)

    return out
