"""Legacy market reads must not turn mutable/current data into scan evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ab_screener import market_regime
from ab_screener.api.routers import legacy_market


def candidate(code="600001.SH", **changes):
    return {"ts_code": code, "name": "测试候选", "industry": "测试行业",
            "trade_date": "20260911", "price": 10.0, "total_score": 88,
            "reasons": "[池A|strict] 已发布证据", "box_high": 9.8, "box_low": 9.0,
            "breakout_date": "2026-09-11", **changes}


class MarketStore:
    def __init__(self, path):
        self.db_path = path
        self.daily_calls = 0

    def load_scan_result(self, *args, **kwargs):
        raise AssertionError("Mutable scan_result must never supply publication evidence")

    def max_trade_date(self, table):
        return "20260911"

    def distinct_dates(self, table, limit=None):
        return ["20260901", "20260910", "20260911"]

    def load_stock_basic(self):
        return pd.DataFrame([
            {"ts_code": code, "name": "最新名称", "industry": "测试行业"}
            for code in ("600001.SH", "600002.SH")
        ])

    def load_daily(self, ts_codes=None, start=None, end=None):
        self.daily_calls += 1
        return pd.DataFrame([
            {"ts_code": code, "trade_date": day, "open": 12.0,
             "high": 13.0, "low": 11.0, "close": 12.5, "vol": 1000.0}
            for code in ts_codes for day in ("20260901", "20260911")
            if (not start or day >= start) and (not end or day <= end)
        ])

    def load_daily_basic(self, ts_codes=None, end=None):
        return pd.DataFrame([{"trade_date": day, "close": 12.0} for day in ("20260901", "20260910")
                             if not end or day <= end])

    def load_moneyflow(self, ts_codes=None, end=None):
        return pd.DataFrame()

    def load_fina_indicator(self, ts_codes=None, limit=None):
        return pd.DataFrame()


@pytest.fixture
def market_client(tmp_path, monkeypatch):
    database = tmp_path / "publication.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE scan_runs (run_id TEXT PRIMARY KEY, task_id TEXT, as_of TEXT, "
                     "strategy_snapshot_json TEXT, config_hash TEXT, dataset_version TEXT, result_hash TEXT, "
                     "git_sha TEXT, status TEXT, created_at TEXT)")
        conn.execute("CREATE TABLE scan_run_candidates (run_id TEXT, stage TEXT, ts_code TEXT, "
                     "total_score REAL, payload_json TEXT)")

    def publish(run_id, rows, *, version=2, state="READY", as_of="20260911", status="SUCCEEDED"):
        snapshot = {"_publication": {"version": version, "state": state,
                    "entry_hash": "frozen-entry", "freshness": {"can_publish_a": True},
                    "regime": {"regime": "neutral", "label": "扫描时环境"},
                    "counts": {"A": len(rows), "B": 0}}} if version is not None else {}
        with sqlite3.connect(database) as conn:
            conn.execute("INSERT INTO scan_runs VALUES (?,?,?,?,?,?,?,?,?,?)", (
                run_id, "task-" + run_id, as_of, json.dumps(snapshot), "config", "data", run_id,
                "code", status, "2026-09-11T18:00:00+08:00",
            ))
            conn.executemany("INSERT INTO scan_run_candidates VALUES (?,?,?,?,?)", [
                (run_id, "final", row["ts_code"], row.get("total_score", 0), json.dumps(row)) for row in rows
            ])

    fresh = {"can_publish_a": True, "is_stale": False, "expected_as_of": "20260911",
             "reference_now": "2026-09-13T12:00:00+08:00", "calendar_verified": True,
             "historical": False}
    calls = []

    def assess(as_of, **kwargs):
        assert set(kwargs) == {"store"}  # no observed-date calendar or historical clock
        calls.append(as_of)
        return {**fresh, "as_of": as_of}

    def no_recalculation(*args, **kwargs):
        raise AssertionError("Current signal or regime computation must not replace scan evidence")

    store = MarketStore(database)
    monkeypatch.setattr(legacy_market, "_store", store)
    monkeypatch.setattr(legacy_market, "_OVERVIEW_CACHE", {"key": None, "payload": None})
    monkeypatch.setattr(legacy_market, "_sig_for", no_recalculation)
    monkeypatch.setattr(legacy_market, "_sig_for_many", no_recalculation)
    monkeypatch.setattr(market_regime, "detect_regime", no_recalculation)
    monkeypatch.setattr(market_regime, "data_freshness", assess)
    app = FastAPI()
    app.include_router(legacy_market.router)
    with TestClient(app) as client:
        yield client, publish, fresh, store, calls


def test_latest_successful_empty_run_replaces_previous_same_date_candidates(market_client):
    client, publish, _, _, _ = market_client
    publish("earlier", [candidate()])
    assert client.get("/api/overview").json()["count"] == 1
    publish("latest-empty", [])
    publish("later-failed", [candidate()], status="FAILED")
    result = client.get("/api/overview").json()
    assert result["count"] == 0
    assert result["items"] == []
    assert result["publication"]["run_id"] == "latest-empty"
    assert result["view_state"] == "CURRENT"
    assert "零只" in result["empty_reason"]
    assert client.get("/api/stock/600001.SH").json()["tier"] == "unknown"


def test_stock_flow_missing_duplicate_and_nonfinite_rows_never_become_zero(market_client, monkeypatch):
    client, _, _, store, _ = market_client
    dates = ["20260907", "20260908", "20260909", "20260910", "20260911"]
    from ab_screener.data import trading_calendar
    monkeypatch.setattr(trading_calendar, "calendar_window", lambda *args, **kwargs: {
        "verified": True, "status": "READY", "required_dates": dates})
    monkeypatch.setattr(legacy_market, "_load_sector_flow", lambda *args, **kwargs: (dates, pd.DataFrame({"其他行业": [1] * 5}, index=dates)))
    row = {"net_mf_amount": 0.0, "buy_elg_amount": 1.0, "buy_lg_amount": 2.0,
           "sell_elg_amount": 1.0, "sell_lg_amount": 2.0}
    monkeypatch.setattr(store, "load_moneyflow", lambda **kwargs: pd.DataFrame([
        {**row, "trade_date": dates[0]}, {**row, "trade_date": dates[2], "net_mf_amount": float("nan")},
        {**row, "trade_date": dates[3]}, {**row, "trade_date": dates[3]},
        {**row, "trade_date": dates[4], "buy_elg_amount": float("inf")},
    ]))
    response = client.get("/api/stock/600001.SH/flow?days=5")
    assert response.status_code == 200
    result = response.json()
    assert [r["status"] for r in result["stock_flow"]] == ["OBSERVED", "MISSING", "INCOMPLETE", "DUPLICATE_DATE", "INCOMPLETE"]
    assert result["stock_flow"][0]["net_wan"] == 0.0
    assert all(result["stock_flow"][i]["net_wan"] is None for i in [1, 2, 3])
    assert result["stock_flow"][4]["buy_main_wan"] is None
    assert result["sector_flow"]["net_wan"] == [None] * 5
    assert result["missing_dates"] == dates[1:]


def test_current_publication_preserves_evidence_and_is_read_only(market_client):
    client, publish, _, store, calls = market_client
    publish("ready", [candidate()])
    before = hashlib.sha256(store.db_path.read_bytes()).digest()
    result = client.get("/api/overview").json()
    item = result["items"][0]
    assert item["price"] == 10.0
    assert item["box_high"] == 9.8
    assert item["kline"][-1]["close"] == 12.5
    assert item["tradeable"] is False and item["trade"] is None
    assert item["candidate_status"] == "CURRENT_CANDIDATE"
    assert result["regime"]["label"] == "扫描时环境"
    assert "candidates" not in result["publication"]
    assert "strategy_snapshot" not in result["publication"]
    assert calls == ["20260911"]
    assert hashlib.sha256(store.db_path.read_bytes()).digest() == before


@pytest.mark.parametrize("version,state", [(None, "READY"), (1, "READY"), (2, "DATA_BLOCKED"), (2, "HISTORICAL")])
def test_unverified_or_blocked_publications_are_only_visible_by_explicit_run(market_client, version, state):
    client, publish, _, _, _ = market_client
    publish("old", [candidate()], version=version, state=state)
    result = client.get("/api/overview").json()
    assert result["items"] == []
    assert result["view_state"] == "HISTORICAL"
    assert result["pool_totals"] == {"A": 0, "B": 0}
    historical = client.get("/api/overview", params={"run_id": "old"}).json()
    assert historical["items"][0]["tier"] == "strict"
    assert historical["items"][0]["candidate_status"] == "HISTORICAL_CANDIDATE"
    assert historical["items"][0]["trade"] is None


def test_current_clock_and_data_gate_are_rechecked_before_cache(market_client):
    client, publish, fresh, store, calls = market_client
    publish("ready", [candidate()])
    assert client.get("/api/overview").json()["count"] == 1
    assert client.get("/api/overview").json()["count"] == 1
    assert store.daily_calls == 1  # identical clock/result may reuse chart
    fresh.update(reference_now="2026-09-14T16:00:00+08:00", expected_as_of="20260914",
                 can_publish_a=False, is_stale=True)
    result = client.get("/api/overview").json()
    assert result["items"] == []
    assert result["freshness"]["reference_now"] == fresh["reference_now"]
    assert len(calls) == 3


def test_dataset_gate_alone_invalidates_current_candidates(market_client):
    client, publish, fresh, _, _ = market_client
    publish("ready", [candidate()])
    assert client.get("/api/overview").json()["count"] == 1
    fresh.update(can_publish_a=False, blocking_reasons=["MONEYFLOW_BEHIND_EXPECTED"])
    assert client.get("/api/overview").json()["items"] == []


def test_free_query_has_no_tier_trade_or_recomputed_signal(market_client):
    client, publish, _, _, _ = market_client
    publish("ready", [candidate()])
    result = client.get("/api/stock/600002.SH").json()
    assert result["tier"] == "unknown"
    assert result["pool"] == result["candidate_status"] == "QUERY_ONLY"
    assert result["tradeable"] is False and result["trade"] is None
    assert result["signal"]["box_high"] is None
    assert result["signal"]["source"] == "UNAVAILABLE"
    assert result["scan_as_of"] is None and result["signal_as_of"] is None
    assert result["quote_as_of"] == "20260911"
    assert result["fundamentals_as_of"] == "20260910"


def test_detail_identifies_only_actual_current_run_members(market_client):
    client, publish, _, _, _ = market_client
    publish("old", [candidate()])
    publish("new", [candidate("600002.SH", reasons="[池B|strict] 名额外观察")])
    assert client.get("/api/stock/600001.SH").json()["tier"] == "unknown"
    result = client.get("/api/stock/600002.SH").json()
    assert result["tier"] == "strict" and result["pool"] == "B"
    assert result["is_current_candidate"] is True
    assert result["tradeable"] is False and result["trade"] is None


def test_explicit_historical_detail_keeps_dates_and_missing_signal_unknown(market_client):
    client, publish, fresh, _, _ = market_client
    row = candidate(trade_date="20260901", breakout_date="2026-09-01")
    del row["box_high"]
    publish("old", [row], as_of="20260901")
    fresh.update(can_publish_a=False, is_stale=True)
    assert client.get("/api/stock/600001.SH").json()["tier"] == "unknown"
    result = client.get("/api/stock/600001.SH", params={"run_id": "old"}).json()
    assert result["tier"] == "strict"
    assert result["view_state"] == "HISTORICAL"
    assert result["signal"]["box_high"] is None
    assert result["scan_as_of"] == result["signal_as_of"] == "20260901"
    assert result["quote_as_of"] == "20260901"
    assert result["fundamentals_as_of"] == "20260901"
    assert result["chart_basis"] == "THROUGH_SCAN_DATE"
    assert all(bar["trade_date"] <= "20260901" for bar in result["kline"])
    assert result["tradeable"] is False


def test_no_publication_and_unknown_run_do_not_fall_back(market_client):
    client, _, _, _, _ = market_client
    result = client.get("/api/overview").json()
    assert result["items"] == [] and result["publication"] is None
    assert result["as_of"] == ""
    assert result["quote_as_of"] == "20260911"
    assert result["view_state"] == "NO_PUBLICATION"
    assert client.get("/api/overview", params={"run_id": "missing"}).status_code == 404
    assert client.get("/api/stock/600001.SH", params={"run_id": "missing"}).status_code == 404


def test_malformed_legacy_pool_marker_never_defaults_to_strict(market_client):
    client, publish, _, _, _ = market_client
    publish("old", [candidate(reasons="[池] 无层级")], version=None)
    result = client.get("/api/stock/600001.SH", params={"run_id": "old"}).json()
    assert result["tier"] == "unknown"
    assert result["tradeable"] is False


def test_freshness_failure_closes_current_view(market_client, monkeypatch):
    client, publish, _, _, _ = market_client
    publish("ready", [candidate()])

    def unavailable(*args, **kwargs):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(market_regime, "data_freshness", unavailable)
    result = client.get("/api/overview").json()
    assert result["items"] == []
    assert result["freshness"]["can_publish_a"] is False
    assert result["freshness"]["blocking_reasons"] == ["FRESHNESS_UNAVAILABLE"]


def test_clock_alone_keeps_chart_cache_but_response_clock_is_current(market_client):
    client, publish, fresh, store, calls = market_client
    publish("ready", [candidate()])
    client.get("/api/overview")
    fresh["reference_now"] = "2026-09-13T12:00:37+08:00"
    result = client.get("/api/overview").json()
    assert result["freshness"]["reference_now"] == fresh["reference_now"]
    assert store.daily_calls == 1
    assert len(calls) == 2


def test_history_overview_does_not_include_later_prices(market_client):
    client, publish, _, _, _ = market_client
    publish("old", [candidate(trade_date="20260901")], as_of="20260901")
    result = client.get("/api/overview", params={"run_id": "old"}).json()
    assert result["chart_basis"] == "THROUGH_SCAN_DATE"
    assert result["items"][0]["kline"][-1]["trade_date"] == "20260901"


def test_candidate_funding_uses_frozen_window_and_free_query_missing_is_null(market_client):
    client, publish, _, _, _ = market_client
    window = {"complete": True, "observed_dates": ["20260907", "20260908", "20260909", "20260910", "20260911"],
              "basis": "net_mf_amount", "denominator_basis": "large_plus_extra_large_buy_and_sell"}
    publish("ready", [candidate(fund_window=window, fund_net_wan=120, fund_score=70, fund_ratio=2.1)])
    overview = client.get("/api/overview").json()["items"][0]
    detail = client.get("/api/stock/600001.SH").json()["fund_flow"]
    assert overview["fund_window"] == detail["window"] == window
    assert detail["source"] == "SCAN_PUBLICATION" and detail["net_wan"] == 120
    query = client.get("/api/stock/600002.SH").json()["fund_flow"]
    assert query["complete"] is False
    assert query["net_wan"] is None and query["ratio_pct"] is None and query["score"] is None


def test_signal_amp_fraction_matches_overview_percent(market_client):
    client, publish, _, _, _ = market_client
    publish("ready", [candidate(box_amp=23.5)])
    assert client.get("/api/overview").json()["items"][0]["box_amp"] == 23.5
    assert client.get("/api/stock/600001.SH").json()["signal"]["box_amp"] == .235


def test_integrity_failure_invalidates_warm_overview_cache(market_client, monkeypatch):
    from ab_screener.application import scan_publication

    client, publish, _, _, _ = market_client
    publish("ready", [candidate()])
    assert client.get("/api/overview").json()["count"] == 1
    original = scan_publication.read_scan_publication

    def damaged(*args, **kwargs):
        result = original(*args, **kwargs)
        return {**result, "verified": False, "qualified_verified": False,
                "qualification_integrity_error": "qualified counts/rules/hash mismatch"}

    monkeypatch.setattr(scan_publication, "read_scan_publication", damaged)
    result = client.get("/api/overview").json()
    assert result["is_current"] is False and result["items"] == []
