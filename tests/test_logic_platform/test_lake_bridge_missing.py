"""lake_bridge 降级测试：路径缺失不崩。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from logic_platform.data.lake_bridge import LakeBridge


def _empty_bridge() -> LakeBridge:
    tmp = tempfile.mkdtemp()
    return LakeBridge(lake_root=Path(tmp) / "nonexistent_lake")


def test_read_day_missing_returns_none():
    b = _empty_bridge()
    assert b.read_day("20260101") is None
    assert b.read_day("20260105") is None


def test_read_day_bad_input_returns_none():
    b = _empty_bridge()
    assert b.read_day("") is None
    assert b.read_day("2026-01-01") is None
    assert b.read_day("abc") is None


def test_status_ok_false_with_missing():
    b = _empty_bridge()
    st = b.status()
    assert st["ok"] is False
    assert "prices/daily" in st["missing"]


def test_read_symbol_history_missing_returns_none():
    b = _empty_bridge()
    assert b.read_symbol_history("000001.SZ", "20260101", "20260110") is None


def test_available_dates_empty():
    b = _empty_bridge()
    assert b.available_dates() == []


def test_norm_columns_when_lake_readable(monkeypatch):
    """湖可读时列归一化：symbol->ts_code, volume->vol, amount=None。"""
    b = _empty_bridge()
    fake = pd.DataFrame({
        "symbol": ["000001.SZ"],
        "trade_date": ["20260105"],
        "open": [10.0], "high": [10.5], "low": [9.9], "close": [10.2],
        "pre_close": [10.0], "change": [0.2], "pct_chg": [2.0],
        "volume": [1000.0], "turnover": [1.0e8],
    })
    monkeypatch.setattr(b, "available_dates", lambda start=None, end=None: ["20260105"])
    monkeypatch.setattr(b, "read_day", lambda d: b._norm(fake) if d == "20260105" else None)
    out = b.read_symbol_history("000001.SZ", "20260105", "20260105")
    assert out is not None
    assert "ts_code" in out.columns and "vol" in out.columns
    assert out.iloc[0]["ts_code"] == "000001.SZ"
    assert out.iloc[0]["vol"] == 1000.0
    assert out.iloc[0]["amount"] is None
