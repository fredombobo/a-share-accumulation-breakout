"""ab_store 测试：隔离市场库取数 + 非法参数防护。"""
from __future__ import annotations

import pytest


def test_ohlcv_has_expected_columns(store):
    df = store.ohlcv("000001.SZ", limit=30)
    for col in ["ts_code", "trade_date", "date", "open", "high", "low",
                "close", "vol"]:
        assert col in df.columns, f"缺列 {col}"


def test_ohlcv_date_format_iso(store):
    df = store.ohlcv("000001.SZ", limit=5)
    if df.empty:
        pytest.skip("本地库无 000001.SZ 数据")
    assert len(df["date"].iloc[-1]) == 10  # YYYY-MM-DD


def test_ohlcv_unknown_code_returns_empty(store):
    df = store.ohlcv("999999.NONE", limit=5)
    assert df.empty


def test_latest_trade_date_yyyymmdd(store):
    d = store.latest_trade_date()
    assert d is None or (len(d) == 8 and d.isdigit())


def test_universe_nonempty(store):
    codes = store.universe_from_stock_basic()
    assert isinstance(codes, list)
    assert len(codes) > 0
