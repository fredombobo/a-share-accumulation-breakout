"""adapters_signals 测试：与 signals.py 结果对齐。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from logic_platform.structure.adapters_signals import (
    box_date_range,
    map_signal_to_state,
)


def test_map_breakout_directly():
    sig = {"is_breakout": True, "box_days": 40, "reasons": ["x"]}
    state, reasons = map_signal_to_state(sig)
    assert state == "BREAKOUT"
    assert reasons[0].startswith("收盘突破")


def test_map_no_box_idle():
    sig = {"is_breakout": False, "box_days": 0, "reasons": ["未找到合格横盘箱体"]}
    state, _reasons = map_signal_to_state(sig)
    assert state == "IDLE"


def test_map_hold_follow_through():
    sig = {"is_breakout": False, "box_days": 40, "cond_hold": True,
           "breakout_date": "2026-04-15", "reasons": []}
    state, _ = map_signal_to_state(sig)
    assert state == "FOLLOW_THROUGH"


def test_map_drop_fail():
    sig = {"is_breakout": False, "box_days": 40, "cond_hold": False,
           "breakout_date": "2026-04-15", "reasons": []}
    state, _ = map_signal_to_state(sig)
    assert state == "FAIL"


def test_map_accumulation_requires_quality():
    sig = {"is_breakout": False, "box_days": 40, "cond_box": True,
           "cond_flat": True, "vol_shrink_ratio": 0.5, "breakout_date": None,
           "reasons": []}
    state, _ = map_signal_to_state(sig)
    assert state == "ACCUMULATION"


def test_map_box_without_flat_is_idle():
    sig = {"is_breakout": False, "box_days": 40, "cond_box": True,
           "cond_flat": False, "vol_shrink_ratio": 0.5, "breakout_date": None,
           "reasons": []}
    state, _ = map_signal_to_state(sig)
    assert state == "IDLE"


def test_box_date_range_mapping():
    df = pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=140).strftime("%Y-%m-%d"),
        "close": np.full(140, 10.0),
    })
    # obs_len = min(140, 125+5+5=135) = 135 → obs_start = 5
    sig = {"box_start_idx": 10, "box_end_idx": 55}
    s, e = box_date_range(df, sig)
    assert s == df["date"].iloc[15]   # 5 + 10
    assert e == df["date"].iloc[60]   # 5 + 55


def test_box_date_range_none_when_no_box():
    df = pd.DataFrame({"date": ["2026-01-05"], "close": [1.0]})
    assert box_date_range(df, {}) == (None, None)
