"""Current-snapshot classification dimensions used by market views."""
from __future__ import annotations

import json

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


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf"), float("-inf"), "invalid"])
def test_sector_aggregation_preserves_missing_groups_and_observed_zero(monkeypatch, missing) -> None:
    store = _MarketStore()
    dates = ["20260909", "20260910", "20260911"]
    rows = pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": dates[0], "net_mf_amount": missing},
        {"ts_code": "300001.SZ", "trade_date": dates[0], "net_mf_amount": 5.0},
        {"ts_code": "300002.SZ", "trade_date": dates[0], "net_mf_amount": -5.0},
        {"ts_code": "300001.SZ", "trade_date": dates[1], "net_mf_amount": 0.0},
        {"ts_code": "300001.SZ", "trade_date": dates[2], "net_mf_amount": 7.0},
        {"ts_code": "300002.SZ", "trade_date": dates[2], "net_mf_amount": missing},
    ])
    monkeypatch.setattr(store, "distinct_dates", lambda *args, **kwargs: dates)
    monkeypatch.setattr(store, "load_moneyflow", lambda **kwargs: rows)
    monkeypatch.setattr(legacy_market, "_store", store)
    monkeypatch.setattr(legacy_market, "_SECTOR_FLOW_CACHE", {})

    result_dates, pivot = legacy_market._load_sector_flow(5)
    assert result_dates == dates
    assert pivot["银行"].isna().all()  # all invalid on day 1; absent on days 2/3
    assert pivot["软件服务"].tolist() == [0.0, 0.0, 7.0]
    result = legacy_market.sector_flow(5)
    assert result["industries"]["银行"] == [None, None, None]
    assert result["groups"] == result["industries"]
    assert result["industries"]["软件服务"] == [0.0, 0.0, 7.0]
    assert all(item["industry"] != "银行" for key in ("top_in", "top_out") for item in result[key])
    assert result["aggregation_basis"] == "AVAILABLE_RECORDS_ONLY"
    json.dumps(result, allow_nan=False)


def test_sector_flow_excludes_nonfinite_ranks_without_dropping_real_zero(monkeypatch) -> None:
    pivot = pd.DataFrame({"未知": [float("nan"), float("inf")], "零值": [0.0, 0.0],
                          "部分观测": [float("-inf"), 2.0]})
    monkeypatch.setattr(legacy_market, "_load_sector_flow", lambda *args, **kwargs: (["20260910", "20260911"], pivot))
    result = legacy_market.sector_flow()
    assert result["industries"] == {"未知": [None, None], "零值": [0.0, 0.0], "部分观测": [None, 2.0]}
    assert {item["industry"]: item["net_wan"] for item in result["top_in"]} == {"部分观测": 2.0, "零值": 0.0}
    assert all(item["industry"] != "未知" for item in result["top_out"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("verified", [True, False])
def test_stock_sector_flow_aligns_only_to_verified_calendar(monkeypatch, verified) -> None:
    from ab_screener.data import trading_calendar

    dates = ["20260907", "20260908", "20260909", "20260910", "20260911"]
    stored_dates = ["20260904", dates[0], dates[-1]]
    store = _MarketStore()
    monkeypatch.setattr(store, "load_moneyflow", lambda **kwargs: pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": dates[0], "net_mf_amount": 0.0},
    ]))
    monkeypatch.setattr(legacy_market, "_store", store)
    monkeypatch.setattr(legacy_market, "_load_sector_flow", lambda *args, **kwargs: (
        stored_dates, pd.DataFrame({"银行": [99.0, 0.0, 2.0]}, index=stored_dates),
    ))
    monkeypatch.setattr(trading_calendar, "calendar_window", lambda *args, **kwargs: {
        "verified": verified, "status": "READY" if verified else "UNVERIFIED", "required_dates": dates,
    })

    result = legacy_market.stock_flow("000001.SZ", days=5)
    sector = result["sector_flow"]
    assert sector["dates"] == (dates if verified else stored_dates)
    assert sector["net_wan"] == ([0.0, None, None, None, 2.0] if verified else [99.0, 0.0, 2.0])
    if verified:
        assert sector["dates"] == [row["trade_date"] for row in result["stock_flow"]]
    assert sector["aggregation_basis"] == "AVAILABLE_RECORDS_ONLY"
    json.dumps(result, allow_nan=False)


def test_unavailable_sector_data_still_preserves_verified_calendar_gaps(monkeypatch) -> None:
    from ab_screener.data import trading_calendar

    dates = ["20260907", "20260908", "20260909", "20260910", "20260911"]
    store = _MarketStore()
    monkeypatch.setattr(store, "load_moneyflow", lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(legacy_market, "_store", store)

    def unavailable(*args, **kwargs):
        raise HTTPException(status_code=500, detail="unavailable")

    monkeypatch.setattr(legacy_market, "_load_sector_flow", unavailable)
    monkeypatch.setattr(trading_calendar, "calendar_window", lambda *args, **kwargs: {
        "verified": True, "status": "READY", "required_dates": dates,
    })
    result = legacy_market.stock_flow("000001.SZ", days=5)
    assert result["sector_flow"]["dates"] == dates
    assert result["sector_flow"]["net_wan"] == [None] * 5
