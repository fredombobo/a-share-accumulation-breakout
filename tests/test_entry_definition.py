"""ENTRY-DEFINITION-V1 单元测试。"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ab_screener.domain.entry_definition import (
    ENTRY_DEFINITION_ID,
    ENTRY_TIMING,
    breakout_in_recent_window,
    definition_snapshot,
    entry_bar_index,
    entry_price_from_bars,
    normalize_breakout_date,
    resolve_entry_from_signal,
    signal_bar_index,
)
from ab_screener.research.attribution import classify_breakout
from trade_sim import _entry_price


def _bars(n: int = 10, start: str = "20260101") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=n).strftime("%Y%m%d").tolist()
    return pd.DataFrame({
        "trade_date": dates,
        "open": [10.0 + i * 0.1 for i in range(n)],
        "high": [10.5 + i * 0.1 for i in range(n)],
        "low": [9.5 + i * 0.1 for i in range(n)],
        "close": [10.2 + i * 0.1 for i in range(n)],
        "vol": [1000.0] * n,
    })


def test_normalize_breakout_date():
    assert normalize_breakout_date("2026-08-07") == "20260807"
    assert normalize_breakout_date("20260807") == "20260807"
    assert normalize_breakout_date(None) == ""
    assert normalize_breakout_date("bad") == ""


def test_signal_and_entry_indices():
    bars = _bars(8)
    bd = bars.iloc[3]["trade_date"]
    sig_i = signal_bar_index(bars, bd)
    assert sig_i == 3
    assert entry_bar_index(sig_i, len(bars)) == 4
    assert entry_bar_index(7, len(bars)) is None  # 最后一根无次日


def test_resolve_entry_from_signal():
    bars = _bars(8)
    bd = bars.iloc[2]["trade_date"]
    sig = {"is_breakout": True, "breakout_date": bd}
    r = resolve_entry_from_signal(bars, sig)
    assert r["ok"] is True
    assert r["signal_index"] == 2
    assert r["entry_index"] == 3
    assert r["entry_timing"] == ENTRY_TIMING
    px = entry_price_from_bars(bars, 2)
    assert px == pytest.approx(float(bars.iloc[3]["open"]))


def test_resolve_rejects_non_breakout():
    bars = _bars(5)
    r = resolve_entry_from_signal(bars, {"is_breakout": False, "breakout_date": bars.iloc[1]["trade_date"]})
    assert r["ok"] is False
    assert r["reason"] == "not_breakout"


def test_breakout_in_recent_window():
    # window=5 → 采样日往前共 6 个日历位 [day_i-5, day_i]
    cal = [
        "20251220", "20251223", "20251224", "20251225", "20251226",
        "20251227", "20251230", "20251231", "20260102", "20260105",
    ]
    assert breakout_in_recent_window("20251231", "20260105", cal, window=5) is True
    assert breakout_in_recent_window("20251220", "20260105", cal, window=5) is False


def test_trade_sim_uses_entry_definition():
    bars = _bars(6)
    # signal day index 1 → entry open at index 2
    assert _entry_price(bars, 1) == pytest.approx(float(bars.iloc[2]["open"]))


def test_definition_snapshot_frozen_id():
    snap = definition_snapshot()
    assert snap["id"] == ENTRY_DEFINITION_ID
    assert snap["entry_timing"] == "next_open"
    assert snap["signal_profile"] == "strict"


def test_classify_breakout_labels():
    assert classify_breakout(0.01, 0.06, 0.08) == "true"
    assert classify_breakout(-0.04, -0.06, -0.08) == "false"
    assert classify_breakout(-0.04, -0.01, 0.0) == "false"
    assert classify_breakout(0.01, 0.01, 0.01) == "mixed"
    assert classify_breakout(None, None, None) == "incomplete"
