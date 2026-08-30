"""Current-snapshot classification dimensions used by market views."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi import HTTPException

from ab_screener.api.routers import legacy_market
from ab_screener.domain.market_classification import (
    classification_catalog,
    get_classification,
)


class _MarketStore:
    def load_stock_basic(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "industry": "银行", "market": "主板", "area": "深圳"},
                {"ts_code": "300001.SZ", "industry": "软件服务", "market": "创业板", "area": "北京"},
                {"ts_code": "300002.SZ", "industry": "软件服务", "market": "创业板", "area": "深圳"},
            ]
        )

    def distinct_dates(self, _table: str, *, limit: int) -> list[str]:
        assert limit >= 6
        return ["20260828"]

    def max_trade_date(self, _table: str) -> str:
        return "20260828"

    def load_moneyflow(self, *, start: str, end: str) -> pd.DataFrame:
        assert start == end == "20260828"
        return pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "20260828", "net_mf_amount": 10.0},
                {"ts_code": "300001.SZ", "trade_date": "20260828", "net_mf_amount": -3.0},
                {"ts_code": "300002.SZ", "trade_date": "20260828", "net_mf_amount": 8.0},
            ]
        )


def test_classification_catalog_only_publishes_real_stock_basic_fields() -> None:
    items = classification_catalog()

    assert [item["key"] for item in items] == ["industry", "market", "area"]
    assert get_classification("industry").title == "细分行业"
    assert all(item["pit_status"] == "CURRENT_SNAPSHOT_ONLY" for item in items)
    with pytest.raises(ValueError, match="不支持的分类标准"):
        get_classification("concept")


def test_sector_flow_can_group_the_same_moneyflow_by_market(monkeypatch) -> None:
    monkeypatch.setattr(legacy_market, "_store", _MarketStore())
    legacy_market._SECTOR_FLOW_CACHE.clear()

    dates, pivot = legacy_market._load_sector_flow(
        5,
        force=True,
        classification="market",
    )

    assert dates == ["20260828"]
    assert pivot.loc["20260828", "主板"] == 10.0
    assert pivot.loc["20260828", "创业板"] == 5.0


def test_classification_api_reports_coverage_and_snapshot_limit(monkeypatch) -> None:
    monkeypatch.setattr(legacy_market, "_store", _MarketStore())

    result = legacy_market.classifications()

    assert result["default"] == "industry"
    assert [item["key"] for item in result["items"]] == ["industry", "market", "area"]
    assert result["items"][1]["group_count"] == 2
    assert result["items"][1]["coverage_pct"] == 100.0
    assert "历史成员" in result["limitations"]


def test_unknown_market_classification_fails_closed() -> None:
    with pytest.raises(HTTPException) as caught:
        legacy_market.money_heatmap(top=10, classification="concept")

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "UNKNOWN_CLASSIFICATION"
