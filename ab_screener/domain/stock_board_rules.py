"""Ordinary-session board rules and deliberately conservative research sizing.

Not IPO/no-limit-session metadata. Mature-cohort research must validate those
exceptions separately; 200-share STAR sizing is a strategy restriction, not the
exchange's actual one-share increment above the 200-share minimum.
"""
from __future__ import annotations

from datetime import datetime
from math import lcm

BOARD_RULE_VERSION = "ordinary-stock-board-v1"


def is_star_stock(code: str) -> bool:
    return code.startswith("688") and code.endswith(".SH")


def ordinary_limit_bps(code: str, trade_date: str) -> int:
    dual = is_star_stock(code) or (code.startswith(("300", "301")) and code.endswith(".SZ"))
    if not dual:
        return 1000  # Compatibility only; not a claim about ST/IPO/ETF rules.
    day = str(trade_date).replace("-", "")[:8]
    datetime.strptime(day, "%Y%m%d")  # Missing/invalid dual-board date fails closed.
    return 2000 if is_star_stock(code) or day >= "20200824" else 1000


def research_quantity_step(code: str, configured_step: int = 100) -> int:
    if isinstance(configured_step, bool) or not isinstance(configured_step, int) or configured_step <= 0:
        raise ValueError("研究数量步长必须为正整数")
    return lcm(configured_step, 200) if is_star_stock(code) else configured_step


def cap_research_buy(code: str, qty: int, step: int) -> int:
    capped = min(qty, 50_000) if is_star_stock(code) else qty
    return max(0, capped // step * step)
