"""Canonical portfolio metric access for professional research results.

Historical research rows used ``net_avg_return`` as a compatibility name for
the portfolio window total return.  Newer accounting rows may expose
``net_total_return`` or ``portfolio_total_return``.  Professional verdicts,
reports and UI contracts must resolve those aliases identically.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

PORTFOLIO_TOTAL_RETURN_KEYS = (
    "portfolio_total_return",
    "net_total_return",
    "net_avg_return",
)


def portfolio_total_return(metrics: Mapping[str, Any] | None) -> float | None:
    """Return one finite portfolio-window total return without inventing zero."""
    if not metrics:
        return None
    for key in PORTFOLIO_TOTAL_RETURN_KEYS:
        value = metrics.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def normalize_portfolio_metrics(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy a metric payload and materialize every supported total-return alias."""
    result = dict(metrics or {})
    total_return = portfolio_total_return(result)
    if total_return is not None:
        for key in PORTFOLIO_TOTAL_RETURN_KEYS:
            result[key] = total_return
    return result
