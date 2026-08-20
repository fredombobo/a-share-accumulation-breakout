"""个股档案（只读）：基础信息 + 最新行情 + 生命周期。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def search_stocks(db_path: str | Path, q: str) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30) as conn:
        rows = conn.execute(
            "SELECT ts_code, name, industry, list_date FROM stock_basic"
            " WHERE ts_code LIKE ? OR name LIKE ? LIMIT 50",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    return [
        {"ts_code": r[0], "name": r[1], "industry": r[2], "list_date": r[3]}
        for r in rows
    ]


def stock_catalog(db_path: str | Path, ts_code: str) -> dict[str, Any]:
    """个股档案：基础信息/生命周期/最新收盘与估值。"""
    with sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30) as conn:
        rule = conn.execute(
            "SELECT name, exchange, security_type, list_date, delist_date"
            " FROM instrument_universe_rules WHERE ts_code=?",
            (ts_code,),
        ).fetchone()
        latest = conn.execute(
            "SELECT trade_date, open, high, low, close, vol, amount FROM daily"
            " WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
            (ts_code,),
        ).fetchone()
        basic = conn.execute(
            "SELECT pe, pb, total_mv FROM daily_basic WHERE ts_code=?"
            " ORDER BY trade_date DESC LIMIT 1",
            (ts_code,),
        ).fetchone()
    return {
        "ts_code": ts_code,
        "instrument": (
            {"name": rule[0], "exchange": rule[1], "security_type": rule[2],
             "list_date": rule[3], "delist_date": rule[4]}
            if rule else None
        ),
        "latest_bar": (
            {"trade_date": latest[0], "open": latest[1], "high": latest[2],
             "low": latest[3], "close": latest[4], "vol": latest[5], "amount": latest[6]}
            if latest else None
        ),
        "latest_valuation": (
            {"pe": basic[0], "pb": basic[1], "total_mv": basic[2]} if basic else None
        ),
    }
