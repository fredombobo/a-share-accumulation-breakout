"""Phase 1 事件研究基元的离线回归测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ab_screener.research.event_study import (
    block_bootstrap_ci,
    cross_section_features,
    next_open_forward_returns,
    select_controls,
    summarize_diffs,
)


def test_forward_return_next_open_entry_and_chained_pct():
    open_ = [10.0, 11.0, 12.1, 12.1, 12.1]
    pre_close = [10.0, 10.0, 11.0, 12.1, 12.1]
    pct = [0.0, 10.0, 10.0, 0.0, 0.0]
    out = next_open_forward_returns(open_, pre_close, pct, horizons=(1, 2))
    # H=1：次日开盘 11 相对调整后前收 10 → +10%，当日收盘出场
    assert out[1][0] == pytest.approx(0.10, abs=1e-9)
    # H=2：open/pre_close(1.10) × (1+r2=1.10) - 1 = 21%
    assert out[2][0] == pytest.approx(0.21, abs=1e-9)
    # 尾部不足 horizon 给 NaN，绝不前向填充
    assert np.isnan(out[1][-1])
    assert np.isnan(out[2][-1])


def test_forward_return_missing_open_yields_nan():
    out = next_open_forward_returns([10.0, 0.0, 11.0], [10.0, 10.0, 10.0], [0.0, 1.0, 1.0], horizons=(1,))
    assert np.isnan(out[1][0])


def test_cross_section_features_shapes_and_nan_warmup():
    close = np.arange(1, 41, dtype=float)
    pct = np.zeros(40)
    feats = cross_section_features(close, pct, np.full(40, 100.0), np.full(40, 1.0), vol_window=20, mom_window=20)
    assert np.isnan(feats["mom20"][19]) and np.isnan(feats["vol20"][18])
    assert feats["mom20"][20] == pytest.approx(close[20] / close[0] - 1)
    assert feats["log_mv"][0] == pytest.approx(np.log(100.0))


def _pool() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "log_mv": [1.0, 1.1, 3.0, 8.0],
            "vol20": [0.10, 0.11, 0.30, 0.60],
            "mom20": [0.00, 0.05, 0.20, 0.50],
            "turnover": [1.0, 1.1, 3.0, 6.0],
        },
        index=[10, 11, 12, 13],
    )


def test_select_controls_picks_similar_and_avoids_outlier():
    event = {"log_mv": 1.05, "vol20": 0.105, "mom20": 0.01, "turnover": 1.05}
    picks, dist = select_controls(event, _pool(), k=2)
    assert set(picks) == {10, 11}
    assert 13 not in picks
    assert dist.iloc[-1] < dist.iloc[0] + 1.0


def test_select_controls_requires_features():
    with pytest.raises(ValueError):
        select_controls({}, _pool(), k=1)


def test_block_bootstrap_clusters_by_date():
    dates = ["20260101", "20260101", "20260102", "20260102"]
    vals = [1.0, 3.0, 5.0, 7.0]
    out = summarize_diffs(dates, vals, reps=500, seed=1)
    assert out["n"] == 4 and out["n_dates"] == 2
    assert out["mean"] == pytest.approx(4.0)
    assert out["lo"] <= out["mean"] <= out["hi"]
    assert 0.0 <= out["positive_rate"] <= 1.0


def test_block_bootstrap_empty_is_nan_without_crash():
    out = block_bootstrap_ci([], [], reps=10)
    assert out["n"] == 0 and np.isnan(out["mean"])
