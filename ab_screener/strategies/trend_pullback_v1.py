"""形态三：上升趋势回踩（v1 插件）。

经济假设：上升趋势中的回调（回踩 MA20）后企稳 → 顺势介入。
失效条件：跌破 MA60（趋势破坏）、回踩后继续下跌。
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

STRATEGY_DEFINITION_ID = "trend_pullback_v1"
VERSION = "v1"

SPEC = plugin_spec(
    strategy_definition_id=STRATEGY_DEFINITION_ID,
    version=VERSION,
    assumption="上升趋势中的回踩 MA20 后企稳为顺势加仓/入场点",
    failure="收盘跌破 MA60（趋势破坏）/ 回踩后继续放量下跌",
    fixture="tests/fixtures/trend_pullback_v1_golden.json（待生成）",
    config_path="configs/strategies/trend_pullback_v1.yaml",
)


def _ma(closes: pd.Series, n: int) -> float | None:
    if len(closes) < n:
        return None
    return float(closes.tail(n).mean())


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
    closes = df["close"].astype(float)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    if ma20 is None or ma60 is None or ma20 <= 0:
        return []
    current = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    if prev is None:
        return []
    tolerance = float(cfg.get("ma20_touch_tolerance", 0.03))
    close = float(current["close"])
    low = float(current["low"])
    # 趋势向上 + 回踩 MA20 附近 + 当日企稳（收盘高于开盘或收阳）
    uptrend = ma20 > ma60 and float(closes.tail(5).mean()) > ma60
    pulled_back = low <= ma20 * (1 + tolerance) and close > ma20 * (1 - tolerance)
    stabilized = close >= float(prev["close"]) or close >= float(current["open"])
    if uptrend and pulled_back and stabilized:
        return [
            build_observation(
                SPEC, ts_code=ts_code, signal_date=str(current["date"]),
                snapshot_id=snapshot_id, input_hash=input_hash, config=cfg,
                payload={"ma20": round(ma20, 4), "ma60": round(ma60, 4), "close": close},
                explanation="上升趋势回踩 MA20 后企稳",
                tradeable=True,
            )
        ]
    return []


register_selection_plugin(SPEC, detect)
