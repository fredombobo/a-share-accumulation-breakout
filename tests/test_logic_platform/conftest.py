"""Hermetic fixtures for logic-platform tests."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from local_store import LocalStore
from logic_platform.data.ab_store import ABStore


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> ABStore:
    """Return a deterministic local market store without reading production data."""
    db_path = tmp_path_factory.mktemp("logic-market") / "market.db"
    local = LocalStore(db_path)
    dates = pd.bdate_range("2025-12-01", periods=180)
    rows: list[dict[str, object]] = []
    previous = 10.0
    for index, date in enumerate(dates):
        close = 10.0 + index * 0.004 + math.sin(index / 7) * 0.18
        rows.append(
            {
                "ts_code": "000001.SZ",
                "trade_date": date.strftime("%Y%m%d"),
                "open": round((previous + close) / 2, 4),
                "high": round(max(previous, close) + 0.08, 4),
                "low": round(min(previous, close) - 0.08, 4),
                "close": round(close, 4),
                "pre_close": round(previous, 4),
                "change": round(close - previous, 4),
                "pct_chg": round((close / previous - 1) * 100, 4),
                "vol": 100_000 + index * 100,
                "amount": round(close * (100_000 + index * 100), 4),
            }
        )
        previous = close
    local.upsert_daily(pd.DataFrame(rows))
    local.upsert_stock_basic(
        pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "测试银行",
                    "area": "深圳",
                    "industry": "银行",
                    "market": "主板",
                    "list_date": "19910403",
                }
            ]
        )
    )
    return ABStore(db_path, migrate=False)
