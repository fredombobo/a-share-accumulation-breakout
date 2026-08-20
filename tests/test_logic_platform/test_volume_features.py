"""volume 特征测试：量比分位、缩量计数、价量相关。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from logic_platform.features.volume_features import compute_volume_features


def _make_df(n=80, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n)
    close = 10 + np.cumsum(rng.normal(0, 0.05, n))
    df = pd.DataFrame({
        "trade_date": dates.strftime("%Y%m%d"),
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "vol": rng.integers(1e6, 5e6, n).astype(float),
        "amount": rng.integers(1e8, 9e8, n).astype(float),
    })
    return df


def test_vol_percentile_in_unit_interval():
    df = _make_df()
    out = compute_volume_features(df)
    v = out["vol_percentile_60"].dropna()
    assert len(v) > 0
    assert ((v >= 0) & (v <= 1)).all()


def test_vol_ma_ratio_5_20_definition():
    df = _make_df()
    out = compute_volume_features(df)
    v5 = df["vol"].rolling(5).mean()
    v20 = df["vol"].rolling(20).mean()
    expected = v5.iloc[-1] / v20.iloc[-1]
    assert out["vol_ma_ratio_5_20"].iloc[-1] == pytest.approx(expected, rel=1e-6)


def test_shrink_days_counting():
    """构造单调递减量能 → 连续缩量天数递增。"""
    df = _make_df(40)
    df["vol"] = np.linspace(5e6, 1e6, len(df))  # 严格递减
    out = compute_volume_features(df)
    assert out["shrink_days"].iloc[-1] >= 20  # 近 20+ 日连续缩量
    assert out["shrink_days"].iloc[0] == 0


def test_amount_missing_ratio_none():
    df = _make_df()
    df = df.drop(columns=["amount"])
    out = compute_volume_features(df)
    assert out["amount_ratio"].isna().all()


def test_vp_corr_bounds():
    df = _make_df()
    out = compute_volume_features(df)
    v = out["vp_corr_20"].dropna()
    assert len(v) > 0
    assert ((v >= -1) & (v <= 1)).all()


def test_breakout_vol_mult_with_box_avg_vol():
    df = _make_df()
    box = {"box_avg_vol": 2e6}
    out = compute_volume_features(df, box=box)
    expected = df["vol"].iloc[-1] / 2e6
    assert out["breakout_vol_mult"].iloc[-1] == pytest.approx(expected, rel=1e-6)
