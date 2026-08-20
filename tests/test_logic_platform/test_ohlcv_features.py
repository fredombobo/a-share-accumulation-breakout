"""ohlcv 特征测试：数值正确性 + 无未来函数。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from logic_platform.features.ohlcv_features import compute_ohlcv_features


def _make_df(n=100, start="2026-01-05", seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    close = 10 + np.cumsum(rng.normal(0, 0.1, n))
    close = np.maximum(close, 1.0)
    df = pd.DataFrame({
        "trade_date": dates.strftime("%Y%m%d"),
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
    })
    df["vol"] = rng.integers(1e6, 5e6, n).astype(float)
    return df


def test_ret_1_correct():
    df = _make_df()
    out = compute_ohlcv_features(df)
    expected = df["close"].iloc[-1] / df["close"].iloc[-2] - 1.0
    assert out["ret_1"].iloc[-1] == pytest.approx(expected, abs=1e-6)


def test_ret_20_correct():
    df = _make_df()
    out = compute_ohlcv_features(df)
    expected = df["close"].iloc[-1] / df["close"].iloc[-21] - 1.0
    assert out["ret_20"].iloc[-1] == pytest.approx(expected, abs=1e-6)


def test_no_lookahead_ret():
    """无未来函数：t 日 ret_1 只依赖 t-1,t；改未来不影响历史值。"""
    df = _make_df(60)
    out = compute_ohlcv_features(df)
    frozen = out["ret_1"].iloc[30]
    df2 = _make_df(60)
    df2.loc[df2.index[-1], "close"] = 999.0  # 篡改最新收盘
    out2 = compute_ohlcv_features(df2)
    assert out2["ret_1"].iloc[30] == frozen


def test_atr_positive_and_finite():
    df = _make_df()
    out = compute_ohlcv_features(df)
    assert out["atr_14"].dropna().gt(0).all()
    assert np.isfinite(out["atr_14"].dropna()).all()


def test_dist_ma20_definition():
    df = _make_df()
    out = compute_ohlcv_features(df)
    ma20 = df["close"].rolling(20).mean()
    expected = df["close"].iloc[-1] / ma20.iloc[-1] - 1.0
    assert out["dist_ma20"].iloc[-1] == pytest.approx(expected, abs=1e-6)


def test_box_features_broadcast():
    df = _make_df()
    box = {"box_amp": 0.064, "box_end_date": df["date"].iloc[-10]}
    out = compute_ohlcv_features(df, box=box)
    assert out["box_amp"].iloc[-1] == pytest.approx(0.064)
    assert out["days_from_box_end"].iloc[-1] == pytest.approx(9.0)


def test_missing_column_raises():
    df = _make_df().drop(columns=["high"])
    with pytest.raises(ValueError):
        compute_ohlcv_features(df)
