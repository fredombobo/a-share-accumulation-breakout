"""市场宽度（只读）：涨跌家数、比率、量能分布。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def market_breadth(db_path: str | Path, trade_date: str) -> dict[str, Any]:
    """单日市场宽度：上涨/下跌/平盘家数与比率（基于 pre_close）。"""
    with sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30) as conn:
        rows = conn.execute(
            "SELECT close, pre_close FROM daily WHERE trade_date=? AND pre_close>0",
            (trade_date,),
        ).fetchall()
    advances = 0
    declines = 0
    unchanged = 0
    for close, pre_close in rows:
        c, p = float(close), float(pre_close)
        if c > p:
            advances += 1
        elif c < p:
            declines += 1
        else:
            unchanged += 1
    total = len(rows)
    return {
        "trade_date": trade_date,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total": total,
        "advance_ratio": round(advances / total, 4) if total else None,
        "advance_decline_ratio": round(advances / declines, 4) if declines else None,
    }
