"""P1.4 市场情报测试：个股档案 + 只读约束。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.instrument_repository import upsert_instrument
from ab_screener.data.migration_registry import apply_pending
from ab_screener.domain.instrument import Instrument
from ab_screener.intelligence.catalog import stock_catalog


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "intel.db"
    conn = sqlite3.connect(str(path))
    try:
        apply_pending(conn)
        upsert_instrument(conn, Instrument(ts_code="000001.SZ", name="平安银行", exchange="SZSE",
                                           security_type="stock", list_date="19910403"))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            " open REAL, high REAL, low REAL, close REAL, pre_close REAL, vol REAL, amount REAL,"
            " PRIMARY KEY (ts_code, trade_date))"
        )
        conn.execute(
            "INSERT INTO daily (ts_code, trade_date, open, high, low, close, pre_close, vol, amount)"
            " VALUES ('000001.SZ','20260810',10.0,10.5,9.8,10.2,9.9,1000,1e7)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_basic (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            " pe REAL, pb REAL, total_mv REAL, PRIMARY KEY (ts_code, trade_date))"
        )
        conn.execute(
            "INSERT INTO daily_basic (ts_code, trade_date, pe, pb, total_mv)"
            " VALUES ('000001.SZ','20260810',8.5,1.2,3e11)"
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


def test_stock_catalog_fields(db: str):
    profile = stock_catalog(db, "000001.SZ")
    assert profile["instrument"] == {
        "name": "平安银行", "exchange": "SZSE", "security_type": "stock",
        "list_date": "19910403", "delist_date": None,
    }
    assert profile["latest_bar"]["trade_date"] == "20260810"
    assert profile["latest_bar"]["close"] == 10.2
    assert profile["latest_valuation"]["pe"] == 8.5


def test_catalog_missing_code_is_empty(db: str):
    profile = stock_catalog(db, "600519.SH")
    assert profile["instrument"] is None and profile["latest_bar"] is None


def test_intelligence_is_read_only(db: str):
    """信息模块只读：调用后库内容不变。"""
    before = {
        r[0]: r[1] for r in sqlite3.connect(db).execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    stock_catalog(db, "000001.SZ")
    after = {
        r[0]: r[1] for r in sqlite3.connect(db).execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert after == before
    count = sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM daily"
    ).fetchone()[0]
    assert count == 1
