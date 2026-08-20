"""形态一：横盘吸筹 → 放量突破（v1 插件，包装 legacy signals 引擎）。

经济假设：长期横盘（机构吸筹）后放量突破箱体上沿 → 趋势启动。
失效条件：假突破（突破后跌破箱体上沿）、无量突破、下降趋势中的中继平台。
PIT：signal_date 为最后收盘 bar；不使用当日之后数据。
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

STRATEGY_DEFINITION_ID = "accumulation_breakout_v1"
VERSION = "v1"

SPEC = plugin_spec(
    strategy_definition_id=STRATEGY_DEFINITION_ID,
    version=VERSION,
    assumption="横盘吸筹后放量突破为趋势启动的可靠先兆（A 池 strict 口径）",
    failure="突破后收盘跌破箱体上沿 / 无量突破 / 下降趋势中继平台",
    fixture="tests/fixtures/entry_v1_golden.json（与 ENTRY V1 golden 对齐）",
    config_path="configs/strategies/accumulation_breakout_v1.yaml",
)


def detect(
    bars: pd.DataFrame,
    config: dict[str, Any] | None = None,
    *,
    ts_code: str,
    snapshot_id: str,
    input_hash: str,
) -> list[SignalObservation]:
    """包装 legacy detect_accumulation_breakout；输出与根 signals.py 逐字段对齐。"""
    from signals import detect_accumulation_breakout

    cfg = config or {}
    df = normalize_bars(bars)
    sig = detect_accumulation_breakout(
        df,
        box_max_days=cfg.get("box_max_days"),
        box_min_days=cfg.get("box_min_days"),
        box_max_amp=cfg.get("box_max_amp"),
        breakout_vol_ratio=cfg.get("breakout_vol_ratio"),
        breakout_chg_min=cfg.get("breakout_chg_min"),
        breakout_chg_max=cfg.get("breakout_chg_max"),
        require_ma60=cfg.get("require_ma60"),
        max_pullbacks=cfg.get("max_pullbacks"),
    )
    if not sig.get("is_breakout"):
        return []
    signal_date = str(sig.get("breakout_date") or df.iloc[-1]["date"])
    return [
        build_observation(
            SPEC,
            ts_code=ts_code,
            signal_date=signal_date,
            snapshot_id=snapshot_id,
            input_hash=input_hash,
            config=cfg,
            payload={
                "box_days": sig.get("box_days"),
                "box_amp": sig.get("box_amp"),
                "box_high": sig.get("box_high"),
                "breakout_vol_ratio": sig.get("breakout_vol_ratio"),
                "breakout_pct_chg": sig.get("breakout_pct_chg"),
                "cond_ma60": sig.get("cond_ma60"),
                "cond_position": sig.get("cond_position"),
            },
            explanation="横盘吸筹后放量突破箱体上沿",
            tradeable=True,
        )
    ]


register_selection_plugin(SPEC, detect)
