"""六形态策略插件包：唯一策略插件契约（P4.1）。"""
from __future__ import annotations

from ab_screener.strategies import (  # noqa: F401  导入即注册六插件
    accumulation_breakout_v1,
    oversold_reversal_v1,
    platform_breakout_v1,
    relative_strength_high_v1,
    trend_pullback_v1,
    volatility_contraction_v1,
)
from ab_screener.strategies.contracts import (  # noqa: F401
    NEXT_TRADABLE_OPEN_EXECUTION_V1,
    SignalObservation,
    StrategySpec,
)
from ab_screener.strategies.registry import (  # noqa: F401
    resolve_selection,
    selection_plugin_ids,
    selection_plugins,
)
