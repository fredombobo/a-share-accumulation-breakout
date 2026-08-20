"""形态六：相对强度高位（v1 插件）。

经济假设：相对大盘/自身创出 N 日新高（相对强度高分位）→ 动量延续。
失效条件：新高后放量滞涨 / 大盘转弱拖累。
PIT：只用信号日及之前数据。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ab_screener.strategies._common import (
    build_observation,
    normalize_bars,
    plugin_spec,
)
from ab_screener.strategies.contracts import SignalObservation
from ab_screener.strategies.registry import register_selection_plugin

STRATEGY_DEFINITION_ID = "relative_strength_high_v1"
VERSION = "v1"

SPEC = plugin_spec(
    strategy_definition_id=STRATEGY_DEFINITION_ID,
    version=VERSION,
    assumption="收盘创 N 日新高（相对强度高分位）为动量延续信号",
    failure="新高后放量滞涨 / 突破幅度过小 / 大盘系统性下跌",
    fixture="tests/fixtures/relative_strength_high_v1_golden.json（待生成）",
    config_path="configs/strategies/relative_strength_high_v1.yaml",
)


def detect(
    bars: pd.DataFrame,
    config: dict[str, Any] | None = None,
    *,
    ts_code: str,
    snapshot_id: str,
    input_hash: str,
) -> list[SignalObservation]:
    cfg = config or {}
    df = normalize_bars(bars)
    lookback = int(cfg.get("lookback", 60))
    min_breakout_pct = float(cfg.get("min_breakout_pct", 0.02))
    if len(df) < lookback + 1:
        return []
    prior = df.iloc[-(lookback + 1):-1]
    prior_high = float(prior["high"].max())
    current = df.iloc[-1]
    close = float(current["close"])
    if prior_high <= 0:
        return []
    breakout_pct = close / prior_high - 1.0
    if close > prior_high and breakout_pct >= min_breakout_pct:
        return [
            build_observation(
                SPEC, ts_code=ts_code, signal_date=str(current["date"]),
                snapshot_id=snapshot_id, input_hash=input_hash, config=cfg,
                payload={"prior_high": prior_high, "breakout_pct": round(breakout_pct, 4)},
                explanation=f"收盘创 {lookback} 日新高（相对强度高位）",
                tradeable=True,
            )
        ]
    return []


register_selection_plugin(SPEC, detect)
