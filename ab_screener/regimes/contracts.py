"""regime overlay 契约（P4.1）。

防守 overlay 不计作第六形态；只改变开仓许可（allow_new_entries）与 WATCHING 展示，
不实现 SignalObservation producer、不直接产生买单。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverlayInput:
    market_regime: str          # defensive / neutral / aggressive
    benchmark_trend: float      # 基准趋势强度（如 MA 斜率）
    drawdown_from_peak: float   # 组合/基准回撤
    allow_new_entries_override: bool | None = None  # 人工覆盖


@dataclass(frozen=True)
class OverlayDecision:
    allow_new_entries: bool
    reason: str
    mode: str  # defensive / neutral / aggressive
