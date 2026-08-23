"""优化器信号缓存的因果时点与去重回归。"""
from __future__ import annotations

import pandas as pd

from optimizer import _detect_signals_for_code


def _bars() -> tuple[pd.DataFrame, list[str]]:
    dates = pd.bdate_range("2026-01-01", periods=100).strftime("%Y%m%d").tolist()
    bars = pd.DataFrame(
        {
            "trade_date": dates,
            "date": pd.to_datetime(dates, format="%Y%m%d").strftime("%Y-%m-%d"),
            "ts_code": "000001.SZ",
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.1,
            "vol": 1000.0,
        }
    )
    return bars, dates


def test_future_confirmed_signal_is_rejected(monkeypatch):
    """采样日才成立、突破当日不成立的信号不得回填过去成交。"""
    import bench_volume
    import signals

    bars, calendar = _bars()
    breakout_day = calendar[-3]
    sample_day = calendar[-1]

    def fake_detect(window, **kwargs):
        last_day = str(window.iloc[-1]["trade_date"])
        return {
            "is_breakout": last_day == sample_day,
            "breakout_date": breakout_day,
            "box_high": 10.0,
            "box_low": 9.0,
        }

    monkeypatch.setattr(signals, "detect_accumulation_breakout", fake_detect)
    monkeypatch.setattr(
        bench_volume,
        "find_build_seqs",
        lambda window, vol_ratio_min: [{"bench_vol": 900.0}],
    )
    result = _detect_signals_for_code(
        bars,
        [sample_day],
        {day: index for index, day in enumerate(calendar)},
        calendar,
        80,
        "A",
        [1.3],
    )
    assert result == []


def test_causal_signal_uses_breakout_day_and_is_deduplicated(monkeypatch):
    """通过突破日因果复验后，以突破日为 signal_date，重叠窗口只保留一次。"""
    import bench_volume
    import signals

    bars, calendar = _bars()
    breakout_day = calendar[-4]
    sample_days = [breakout_day, calendar[-2], calendar[-1]]

    monkeypatch.setattr(
        signals,
        "detect_accumulation_breakout",
        lambda window, **kwargs: {
            "is_breakout": True,
            "breakout_date": breakout_day,
            "box_high": 10.0,
            "box_low": 9.0,
        },
    )
    monkeypatch.setattr(
        bench_volume,
        "find_build_seqs",
        lambda window, vol_ratio_min: [{"bench_vol": 900.0}],
    )

    result = _detect_signals_for_code(
        bars,
        sample_days,
        {day: index for index, day in enumerate(calendar)},
        calendar,
        80,
        "A",
        [1.3],
    )

    assert len(result) == 1
    assert result[0]["day"] == breakout_day
    assert result[0]["breakout_date"] == breakout_day
    assert result[0]["discovered_on"] == sample_days[0]
