"""价格类特征（docs §4.1 价格特征第一版）。

全部特征只用 t 及以前的数据（无未来函数）：
  ret_1/5/20      收益率（动量/反转）
  atr_14          真实波幅（止损/仓位）
  box_amp         箱体振幅（来自 signals 结果，稳健去影线）
  dist_ma20/60    相对均线偏离（过热/乖离）
  days_from_box_end 距箱体右端交易日数（时效）
  dist_high_60    距 60 日高（近高突破）

入参 df 需含列：trade_date, date, open, high, low, close（升序）。
box 为 signals.detect_accumulation_breakout 结果 dict（可 None）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_REQUIRED = ["trade_date", "date", "open", "high", "low", "close"]


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def compute_ohlcv_features(
    df: pd.DataFrame, box: dict | None = None
) -> pd.DataFrame:
    """在 df 上追加价格类特征列，返回副本（原 df 不改）。"""
    out = df.copy()
    for col in _REQUIRED:
        if col not in out.columns:
            raise ValueError(f"缺列: {col}")

    close = pd.to_numeric(out["close"], errors="coerce")

    out["ret_1"] = close.pct_change(1)
    out["ret_5"] = close.pct_change(5)
    out["ret_20"] = close.pct_change(20)

    atr = _true_range(out).rolling(14).mean()
    out["atr_14"] = atr

    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    out["dist_ma20"] = close / ma20 - 1.0
    out["dist_ma60"] = close / ma60 - 1.0

    high60 = pd.to_numeric(out["high"], errors="coerce").rolling(60).max()
    out["dist_high_60"] = close / high60 - 1.0

    # box 相关特征（来自 signals 结构结果，整列广播）
    out["box_amp"] = np.nan
    out["days_from_box_end"] = np.nan
    if box and box.get("box_amp") is not None:
        out["box_amp"] = float(box["box_amp"])
    if box and box.get("box_end_date"):
        end_date = box["box_end_date"]
        # 距箱体右端交易日数：最新日向前数到 end_date 的日数
        try:
            idx = out.index[out["date"] == end_date]
            if len(idx):
                days = len(out) - 1 - out.index.get_loc(idx[0])
                out["days_from_box_end"] = float(days)
        except Exception:  # noqa: BLE001
            out["days_from_box_end"] = np.nan

    return out
