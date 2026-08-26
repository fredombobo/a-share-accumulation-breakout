"""A 股主要指数快照（只读，本地 daily）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

A_SHARE_INDICES: tuple[tuple[str, str], ...] = (
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
)


def index_snapshot(db_path: str | Path, trade_date: str) -> dict[str, Any]:
    path = Path(db_path)
    codes = [c for c, _ in A_SHARE_INDICES]
    if not path.is_file():
        return {"trade_date": trade_date, "status": "INSUFFICIENT", "reason": "db_missing", "items": []}
    placeholders = ",".join("?" * len(codes))
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        rows = conn.execute(
            f"SELECT ts_code, close, pre_close FROM daily"
            f" WHERE trade_date=? AND ts_code IN ({placeholders})",
            (trade_date, *codes),
        ).fetchall()
    by_code = {str(r[0]): r for r in rows}
    items: list[dict[str, Any]] = []
    for code, name in A_SHARE_INDICES:
        row = by_code.get(code)
        if not row or row[1] is None:
            continue
        close = float(row[1])
        pre = float(row[2]) if row[2] else None
        pct = None
        if pre and pre > 0:
            pct = round((close / pre - 1.0) * 100.0, 2)
        items.append({
            "ts_code": code,
            "name": name,
            "close": close,
            "pct_chg": pct,
        })
    if not items:
        return {
            "trade_date": trade_date,
            "status": "INSUFFICIENT",
            "reason": "no_index_bars",
            "items": [],
        }
    return {
        "trade_date": trade_date,
        "status": "PASS",
        "reason": None,
        "items": items,
        "coverage": round(len(items) / len(A_SHARE_INDICES), 4),
    }
