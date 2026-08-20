"""形态二：波动收缩 → 突破（v1 插件）。

经济假设：波动率收缩（BB 带宽收窄）后价格突破近期区间上沿 → 波动扩张启动。
失效条件：收缩后向下突破、量能不配合、横盘后继续横盘。
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

STRATEGY_DEFINITION_ID = "volatility_contraction_v1"
VERSION = "v1"

SPEC = plugin_spec(
    strategy_definition_id=STRATEGY_DEFINITION_ID,
    version=VERSION,
    assumption="波动率收缩后向上突破为波动扩张与趋势启动的先兆",
    failure="向下突破 / 突破无量 / 收缩后横盘不启动",
    fixture="tests/fixtures/volatility_contraction_v1_golden.json（待生成）",
    config_path="configs/strategies/volatility_contraction_v1.yaml",
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
    lookback = int(cfg.get("lookback", 20))
    vol_shrink_pct = float(cfg.get("vol_shrink_pct", 0.5))
    if len(df) < lookback + 1:
        return []
    window = df.iloc[-(lookback + 1):-1]
    closes = window["close"].astype(float)
    std = float(closes.std())
    mean = float(closes.mean())
    if mean <= 0 or std <= 0:
        return []
    current = df.iloc[-1]
    recent_high = float(window["high"].max())
    # 波动收缩：近段 std/mean 低于中段
    earlier = df.iloc[-(lookback * 2 + 1):-lookback]["close"].astype(float)
    ratio = std / mean / (float(earlier.std()) / max(float(earlier.mean()), 1e-9)) \
        if len(earlier) >= 5 and float(earlier.std()) > 0 else 1.0
    if ratio <= vol_shrink_pct and float(current["close"]) > recent_high:
        return [
            build_observation(
                SPEC, ts_code=ts_code, signal_date=str(current["date"]),
                snapshot_id=snapshot_id, input_hash=input_hash, config=cfg,
                payload={"volatility_ratio": round(ratio, 4), "recent_high": recent_high},
                explanation="波动收缩后突破近期高点",
                tradeable=True,
            )
        ]
    return []


register_selection_plugin(SPEC, detect)
