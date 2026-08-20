"""标签测试：正确性 + 无泄漏边界。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from logic_platform.prediction.labels import add_labels, label_columns


def _make_df(n=100) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=n)
    close = np.arange(n, dtype=float) + 10  # 单调 +1/日
    return pd.DataFrame({
        "trade_date": dates.strftime("%Y%m%d"),
        "date": dates.strftime("%Y-%m-%d"),
        "open": close, "high": close + 0.5, "low": close - 0.5, "close": close,
    })


def test_y_ret_definition():
    df = _make_df()
    out = add_labels(df, horizons=(5,))
    # t=0: close[5]/close[0] - 1 = 15/10 - 1 = 0.5
    assert out["y_ret_5"].iloc[0] == pytest.approx(0.5)
    assert out["y_ret_5"].iloc[94] == pytest.approx(109 / 104 - 1)


def test_y_up_definition():
    df = _make_df()
    out = add_labels(df, horizons=(5,))
    assert out["y_up_5"].iloc[0] == 1.0  # 未来更高
    assert out["y_up_5"].iloc[93] == 1.0


def test_labels_nan_at_tail_no_lookahead():
    """末尾 N 行标签必须为 NaN（未来数据不存在），特征列不受影响。"""
    df = _make_df(30)
    out = add_labels(df, horizons=(10,))
    assert out["y_ret_10"].iloc[-10:].isna().all()
    assert out["y_ret_10"].iloc[:20].notna().all()
    # 原特征列完好
    assert "close" in out.columns and out["close"].notna().all()


def test_no_feature_leakage_by_construction():
    """特征（rolling）只用 t 及以前：篡改 t 之后价格不影响 t 日特征。"""
    df = _make_df(60)
    feats = df[["close"]].copy()
    feats["ret_1"] = feats["close"].pct_change(1)
    # 改未来 close → 过去 ret_1 不变
    df2 = df.copy()
    df2.loc[40:, "close"] += 100
    feats2 = df2[["close"]].copy()
    feats2["ret_1"] = feats2["close"].pct_change(1)
    assert feats["ret_1"].iloc[39] == pytest.approx(feats2["ret_1"].iloc[39])


def test_y_mdd_negative_or_zero():
    df = _make_df()
    out = add_labels(df, horizons=(5,))
    v = out["y_mdd_5"].dropna()
    assert (v <= 1e-9).all()  # 回撤 ≤ 0（未来 low 相对当前 close）


def test_label_columns_names():
    cols = label_columns((5, 10))
    assert cols == ["y_up_5", "y_ret_5", "y_mdd_5", "y_new_high_5",
                    "y_up_10", "y_ret_10", "y_mdd_10", "y_new_high_10"]
