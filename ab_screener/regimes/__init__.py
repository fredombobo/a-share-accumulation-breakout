"""防守 overlay 包（P4.1）：只改变开仓许可与展示，不产生买单。"""
from __future__ import annotations

from ab_screener.regimes.contracts import OverlayDecision  # noqa: F401
from ab_screener.regimes.defensive_overlay_v1 import (  # noqa: F401
    DEFENSIVE_OVERLAY_ID,
    evaluate,
)
from ab_screener.regimes.registry import (  # noqa: F401
    regime_overlays,
    resolve_overlay,
)
