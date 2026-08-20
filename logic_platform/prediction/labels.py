"""预测标签（docs §5.1）：未来 N 日方向/收益/回撤/新高。

无泄漏纪律：
  - 特征用 t 及以前；标签用 t+N 及以后（shift 未来）
  - t 日标签 = close[t+N] / close[t] - 1（非 t+1 起点）
  - 每只股票内部生成，窗口不足的样本丢弃（label=NaN 过滤）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_HORIZONS = (5, 10, 20)


def add_labels(df: pd.DataFrame, horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> pd.DataFrame:
    """在 df 上追加未来 N 日标签列，返回副本。

    入参 df 需含（升序）：trade_date, open, high, low, close。
    输出标签（每 horizon N）：
      y_up_N      close[t+N] > close[t]（1/0）
      y_ret_N     close[t+N] / close[t] - 1（float）
      y_mdd_N     未来 N 日最低 low 相对 close[t] 的最大回撤（≤0）
      y_new_high_N  close[t+N] > max(high[t-59..t])（创新高，1/0）
    """
    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")

    for n in horizons:
        fut_close = close.shift(-n)
        y_ret = fut_close / close - 1.0
        # 未来窗口（t+1..t+n）最低 low：shift(-1) 后反转做前向 rolling min，再反转回原轴
        fut_low_min = (
            low.shift(-1).iloc[::-1].rolling(n, min_periods=1).min().iloc[::-1]
        )
        y_mdd = np.minimum(fut_low_min / close - 1.0, 0.0)  # 回撤语义 ≤0
        # 近 60 日最高 high（t-59..t）
        high_60 = high.rolling(60, min_periods=1).max()
        y_new_high = (fut_close > high_60).astype(float)
        y_up = (fut_close > close).astype(float)

        out[f"y_up_{n}"] = np.where(pd.isna(y_ret), np.nan, y_up)
        out[f"y_ret_{n}"] = y_ret
        out[f"y_mdd_{n}"] = y_mdd
        out[f"y_new_high_{n}"] = np.where(pd.isna(y_ret), np.nan, y_new_high)
    return out


def label_columns(horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> list[str]:
    """标签列名列表。"""
    cols: list[str] = []
    for n in horizons:
        cols += [f"y_up_{n}", f"y_ret_{n}", f"y_mdd_{n}", f"y_new_high_{n}"]
    return cols
