"""P4.1 旧策略 parity 测试：accumulation_breakout_v1 插件与根 signals 引擎一致。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.strategies.accumulation_breakout_v1 import (
    STRATEGY_DEFINITION_ID,
    detect,
)
from signals import detect_accumulation_breakout
from test_signals import make_synthetic


def test_plugin_matches_legacy_engine_on_synthetic():
    """同一 synthetic fixture：插件 is_breakout 与根引擎一致（parity）。"""
    df = make_synthetic(seed=42, flat_days=80)
    legacy = detect_accumulation_breakout(df)
    observations = detect(df, None, ts_code="000001.SZ",
                          snapshot_id="snap1", input_hash="h1")
    assert bool(legacy.get("is_breakout")) == bool(observations)
    if observations:
        obs = observations[0]
        assert obs.strategy_definition_id == STRATEGY_DEFINITION_ID
        assert obs.payload["box_days"] == legacy.get("box_days")
        assert obs.payload["breakout_vol_ratio"] == legacy.get("breakout_vol_ratio")
        assert obs.explanation.strip()


def test_plugin_flat_no_signal():
    """无形态（纯震荡无突破）→ 插件零观察（与根引擎一致）。"""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(3)
    df = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=120, freq="B").strftime("%Y%m%d"),
            "open": 10.0 + rng.normal(0, 0.05, 120),
            "high": 10.3, "low": 9.7, "close": 10.0 + rng.normal(0, 0.05, 120),
            "vol": 100_000, "amount": 1e8,
        }
    )
    legacy = detect_accumulation_breakout(df)
    observations = detect(df, None, ts_code="000001.SZ",
                          snapshot_id="snap1", input_hash="h1")
    assert bool(legacy.get("is_breakout")) == bool(observations)
