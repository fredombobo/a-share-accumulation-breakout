"""P1.4 市场宽度测试：涨跌家数与比率。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.intelligence.breadth import market_breadth


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "breadth.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            " close REAL, pre_close REAL, vol REAL, amount REAL,"
            " PRIMARY KEY (ts_code, trade_date))"
        )
        conn.executemany(
            "INSERT INTO daily (ts_code, trade_date, close, pre_close, vol, amount)"
            " VALUES (?,?,?,?,?,?)",
            [
                ("000001.SZ", "20260810", 10.2, 9.9, 1000, 1e7),   # 涨
                ("000002.SZ", "20260810", 5.0, 5.0, 800, 1e6),     # 平
                ("000003.SZ", "20260810", 3.0, 3.2, 600, 1e6),     # 跌
                ("000004.SZ", "20260810", 8.8, 8.0, 900, 1e6),     # 涨
                ("000005.SZ", "20260810", 2.1, 2.5, 400, 1e5),     # 跌
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


def test_market_breadth_counts(db: str):
    b = market_breadth(db, "20260810")
    assert b["advances"] == 2
    assert b["declines"] == 2
    assert b["unchanged"] == 1
    assert b["total"] == 5
    assert b["advance_ratio"] == 0.4
    assert b["advance_decline_ratio"] == 1.0


def test_breadth_empty_day(db: str):
    b = market_breadth(db, "19990101")
    assert b["total"] == 0 and b["advance_ratio"] is None
