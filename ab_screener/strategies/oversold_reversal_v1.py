"""形态五：超卖反转（v1 插件）。

经济假设：RSI 超卖（<30）后出现企稳/反包 → 短期反转。
失效条件：超卖后继续阴跌（趋势性下跌不接飞刀）、无反转确认。
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

STRATEGY_DEFINITION_ID = "oversold_reversal_v1"
VERSION = "v1"

SPEC = plugin_spec(
    strategy_definition_id=STRATEGY_DEFINITION_ID,
    version=VERSION,
    assumption="RSI 超卖后的企稳反包为短期反转起点（均值回归）",
    failure="超卖后继续放量下跌 / 无收盘反转确认",
    fixture="tests/fixtures/oversold_reversal_v1_golden.json（待生成）",
    config_path="configs/strategies/oversold_reversal_v1.yaml",
)


def _rsi(closes: pd.Series, n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    delta = closes.diff().dropna().tail(n)
    gains = delta.clip(lower=0).mean()
    losses = (-delta.clip(upper=0)).mean()
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


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
    rsi = _rsi(df["close"].astype(float), int(cfg.get("rsi_period", 14)))
    if rsi is None:
        return []
    current = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    if prev is None:
        return []
    oversold = rsi < float(cfg.get("oversold_threshold", 30.0))
    reversal = float(current["close"]) > float(prev["close"]) and \
        float(current["close"]) >= float(current["open"])
    if oversold and reversal:
        return [
            build_observation(
                SPEC, ts_code=ts_code, signal_date=str(current["date"]),
                snapshot_id=snapshot_id, input_hash=input_hash, config=cfg,
                payload={"rsi": round(rsi, 2)},
                explanation="RSI 超卖后企稳反包",
                tradeable=True,
            )
        ]
    return []


register_selection_plugin(SPEC, detect)
