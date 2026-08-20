"""Deterministic market-risk fixtures for paper-trading tests."""
from __future__ import annotations

import sqlite3


def seed_fresh_neutral_benchmark(
    conn: sqlite3.Connection,
    *,
    latest: str = "20260807",
) -> None:
    """Seed a flat 30-bar CSI 300 series current through ``latest``."""
    dates = [f"202607{day:02d}" for day in range(1, 30)] + [latest]
    conn.executemany(
        """
        INSERT OR REPLACE INTO daily
        (ts_code, trade_date, open, high, low, close, vol, amount)
        VALUES ('000300.SH', ?, 4000, 4010, 3990, 4000, 1000000, 400000000)
        """,
        [(trade_date,) for trade_date in dates],
    )
