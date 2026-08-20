"""形态四：平台突破（v1 插件）。

经济假设：N 日窄幅平台整理后收盘突破平台高点 → 平台向上突破。
失效条件：平台过短/过宽（非整理）、突破无量、假突破。
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

STRATEGY_DEFINITION_ID = "platform_breakout_v1"
VERSION = "v1"

SPEC = plugin_spec(
    strategy_definition_id=STRATEGY_DEFINITION_ID,
    version=VERSION,
    assumption="窄幅平台整理后放量突破为向上启动信号",
    failure="平台过宽（非整理）/ 突破无量 / 突破后回落平台内",
    fixture="tests/fixtures/platform_breakout_v1_golden.json（待生成）",
    config_path="configs/strategies/platform_breakout_v1.yaml",
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
    platform_days = int(cfg.get("platform_days", 15))
    max_amp = float(cfg.get("max_amp", 0.12))
    vol_ratio = float(cfg.get("vol_ratio", 1.5))
    if len(df) < platform_days + 2:
        return []
    platform = df.iloc[-(platform_days + 1):-1]
    high = float(platform["high"].max())
    low = float(platform["low"].min())
    mid = (high + low) / 2.0
    amp = (high - low) / mid if mid > 0 else 1.0
    current = df.iloc[-1]
    avg_vol = float(platform["vol"].mean())
    if amp <= max_amp and float(current["close"]) > high and avg_vol > 0 \
            and float(current["vol"]) >= avg_vol * vol_ratio:
        return [
            build_observation(
                SPEC, ts_code=ts_code, signal_date=str(current["date"]),
                snapshot_id=snapshot_id, input_hash=input_hash, config=cfg,
                payload={"platform_amp": round(amp, 4), "platform_high": high,
                         "vol_ratio_vs_avg": round(float(current["vol"]) / avg_vol, 2)},
                explanation="窄幅平台放量突破",
                tradeable=True,
            )
        ]
    return []


register_selection_plugin(SPEC, detect)
