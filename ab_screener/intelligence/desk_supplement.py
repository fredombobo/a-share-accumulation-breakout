"""指挥舱情报补充（astock 口径，只读）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ab_screener.integrations.astock_client import probe_astock
from ab_screener.intelligence.breadth import market_breadth
from ab_screener.intelligence.indices import index_snapshot
from ab_screener.intelligence.limit_up import limit_up_ladder


def latest_trade_date(db_path: str | Path) -> str | None:
    path = Path(db_path)
    if not path.is_file():
        return None
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def build_desk_supplement(
    db_path: str | Path,
    trade_date: str | None = None,
    *,
    astock_base_url: str | None = None,
    include_http: bool = True,
) -> dict[str, Any]:
    as_of = (trade_date or "").strip() or latest_trade_date(db_path)
    if not as_of:
        return {
            "side_effects": False,
            "not_a_pool": True,
            "trade_date": None,
            "status": "INSUFFICIENT",
            "reason": "no_trade_date",
            "disclaimer": "研究情报，不是买卖指令，不进入 A 池。",
        }
    breadth = market_breadth(db_path, as_of)
    ladder = limit_up_ladder(db_path, as_of)
    indices = index_snapshot(db_path, as_of)
    astock = (
        probe_astock(astock_base_url)
        if include_http
        else {"enabled": False, "reachable": False, "base_url": "", "global": None, "error": None}
    )
    local_ok = breadth.get("total", 0) > 0
    status = "PASS" if local_ok else "INSUFFICIENT"
    return {
        "side_effects": False,
        "not_a_pool": True,
        "trade_date": as_of,
        "status": status,
        "reason": None if local_ok else "empty_breadth",
        "breadth": breadth,
        "limit_up": ladder,
        "indices": indices,
        "astock": astock,
        "disclaimer": "研究情报，不是买卖指令，不进入 A 池。",
    }
