"""Independent-calendar and per-stock window checks; temporary fixtures only."""
from __future__ import annotations

import sqlite3
import types
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ab_screener.data.freshness import moneyflow_rows_for_window, moneyflow_window_status
from ab_screener.market_regime import data_freshness
from pool_select import fund_flow_quality_ok, split_pools

SHANGHAI = ZoneInfo("Asia/Shanghai")
WINDOW = ["20260907", "20260908", "20260909", "20260910", "20260911"]


@pytest.fixture()
def calendar_store(tmp_path):
    path = tmp_path / "calendar.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(cal_date TEXT PRIMARY KEY,is_open INTEGER,source TEXT)")
        cursor = date(2026, 8, 1)
        while cursor <= date(2026, 9, 15):
            conn.execute("INSERT INTO trade_cal VALUES(?,?,?)", (cursor.strftime("%Y%m%d"), int(cursor.weekday() < 5), "tushare"))
            cursor += timedelta(days=1)
        # Both stock and benchmark stop on the same date. Neither defines the calendar.
        conn.execute("CREATE TABLE daily(ts_code TEXT,trade_date TEXT)")
        conn.executemany("INSERT INTO daily VALUES(?,?)", [("000001.SZ", "20260904"), ("000300.SH", "20260904")])
        for table in ("daily_basic", "moneyflow"):
            conn.execute(f"CREATE TABLE {table}(ts_code TEXT,trade_date TEXT)")
            conn.execute(f"INSERT INTO {table} VALUES(?,?)", ("000001.SZ", "20260914"))
    return types.SimpleNamespace(db_path=path, distinct_dates=lambda _: ["20260901", "20260902", "20260903", "20260904"])


def moneyflow(dates=WINDOW):
    return pd.DataFrame({"trade_date": dates, "net_mf_amount": [10.] * len(dates), "amount": [1000.] * len(dates)})


def test_joint_quote_outage_cannot_certify_itself_from_observed_dates(calendar_store):
    result = data_freshness("20260904", trade_dates=calendar_store.distinct_dates("daily"),
                            store=calendar_store, now=datetime(2026, 9, 13, 18, tzinfo=SHANGHAI))
    assert result["expected_as_of"] == "20260911"
    assert result["stale_days"] == 5
    assert result["is_stale"] is True
    assert result["calendar_verified"] is True
    assert result["can_publish_a"] is False
    assert result["required_moneyflow_dates"] == WINDOW


def test_calendar_missing_cannot_fall_back_to_daily_or_create_a_database(tmp_path):
    missing = tmp_path / "does-not-exist.db"
    result = data_freshness("20260911", store=types.SimpleNamespace(db_path=missing),
                            trade_dates=WINDOW, now=datetime(2026, 9, 13, 18, tzinfo=SHANGHAI))
    assert result["can_publish_a"] is False
    assert result["calendar_verified"] is False
    assert result["expected_as_of"] == ""
    assert not missing.exists()


@pytest.mark.parametrize("corruption,expected_status", [
    ("DELETE FROM trade_cal WHERE cal_date='20260912'", "CALENDAR_INCOMPLETE"),
    ("UPDATE trade_cal SET source='local_infer' WHERE cal_date='20260912'", "CALENDAR_UNVERIFIED"),
])
def test_closed_day_coverage_and_provenance_are_required(calendar_store, corruption, expected_status):
    with sqlite3.connect(calendar_store.db_path) as conn:
        conn.execute(corruption)
    result = data_freshness("20260911", store=calendar_store, now=datetime(2026, 9, 13, 18, tzinfo=SHANGHAI))
    assert result["calendar_status"] == expected_status
    assert result["can_publish_a"] is False
    assert result["required_moneyflow_dates"] == []


def test_independent_calendar_handles_weekends_and_a_fixture_holiday(calendar_store):
    weekend = data_freshness("20260911", store=calendar_store, now=datetime(2026, 9, 13, 18, tzinfo=SHANGHAI))
    assert weekend["can_publish_a"] is True
    # Synthetic holiday: test calendar semantics without embedding a holiday approximation.
    with sqlite3.connect(calendar_store.db_path) as conn:
        conn.execute("UPDATE trade_cal SET is_open=0 WHERE cal_date='20260914'")
    holiday = data_freshness("20260911", store=calendar_store, now=datetime(2026, 9, 14, 18, tzinfo=SHANGHAI))
    assert holiday["can_publish_a"] is True
    assert holiday["expected_as_of"] == "20260911"


def test_expected_day_changes_at_existing_1600_cutoff_and_one_day_lag_blocks(calendar_store):
    before = data_freshness("20260911", store=calendar_store, now=datetime(2026, 9, 14, 15, 59, tzinfo=SHANGHAI))
    after = data_freshness("20260911", store=calendar_store, now=datetime(2026, 9, 14, 16, tzinfo=SHANGHAI))
    assert before["can_publish_a"] is True
    assert after["expected_as_of"] == "20260914"
    assert after["stale_days"] == 1
    assert after["can_publish_a"] is False
    assert after["is_stale"] is True
    utc = data_freshness("20260914", store=calendar_store, now=datetime(2026, 9, 14, 8, tzinfo=UTC))
    assert utc["can_publish_a"] is True


@pytest.mark.parametrize("hour,minute,expected", [(15, 59, "20260911"), (16, 0, "20260914")])
def test_naive_legacy_clock_is_interpreted_as_shanghai(calendar_store, hour, minute, expected):
    # Deliberately exercise the legacy naive-input contract at the 16:00 boundary.
    naive = datetime(2026, 9, 14, hour, minute)  # noqa: DTZ001
    result = data_freshness("20260911", store=calendar_store, now=naive)
    aware = data_freshness("20260911", store=calendar_store, now=naive.replace(tzinfo=SHANGHAI))
    assert result == aware
    assert result["expected_as_of"] == expected


def test_explicit_replay_clock_allows_historical_evaluation(calendar_store):
    replay = data_freshness("20260904", store=calendar_store, historical=True)
    explicit = data_freshness("20260904", store=calendar_store, reference_now=datetime(2026, 9, 4, 18, tzinfo=SHANGHAI))
    assert replay["historical"] is True
    assert replay["can_publish_a"] is True
    assert replay["expected_as_of"] == explicit["expected_as_of"] == "20260904"
    assert explicit["can_publish_a"] is True


def test_future_or_non_session_observations_do_not_become_current(calendar_store):
    future = data_freshness("20260914", store=calendar_store, now=datetime(2026, 9, 13, 18, tzinfo=SHANGHAI))
    non_session = data_freshness("20260912", store=calendar_store, now=datetime(2026, 9, 13, 18, tzinfo=SHANGHAI))
    assert future["can_publish_a"] is non_session["can_publish_a"] is False
    assert future["as_of"] == "20260914"
    assert non_session["as_of"] == "20260912"  # Never silently align the stored date to Friday.


@pytest.mark.parametrize("table", ["daily_basic", "moneyflow"])
@pytest.mark.parametrize("failure,status", [
    ("UPDATE {table} SET trade_date='20260910'", "BEHIND_EXPECTED"),
    ("DELETE FROM {table}", "MISSING"),
    ("DROP TABLE {table}", "UNAVAILABLE"),
])
def test_current_quotes_do_not_hide_stale_or_missing_required_dataset(calendar_store, table, failure, status):
    with sqlite3.connect(calendar_store.db_path) as conn:
        conn.execute(failure.format(table=table))
    result = data_freshness("20260911", store=calendar_store, now=datetime(2026, 9, 13, 18, tzinfo=SHANGHAI))
    assert result["expected_as_of"] == "20260911"
    assert result["stale_days"] == 0
    assert result["can_publish_a"] is False
    assert result["is_current"] is False
    assert result["label"] == result["stale_label"] == "数据未齐"
    assert f"{table.upper()}_{status}" in result["blocking_reasons"]
    assert result["dataset_freshness"][table]["is_current"] is False


def test_b_pool_preserves_withheld_strict_before_high_score_fillers_and_reports_truncation():
    candidates = pd.DataFrame([
        {"ts_code": "strict-low", "筛选层级": "strict", "综合分": 60},
        {"ts_code": "strict-high", "筛选层级": "strict", "综合分": 80},
        {"ts_code": "strict-mid", "筛选层级": "strict", "综合分": 70},
        {"ts_code": "filler", "筛选层级": "theme_fill", "综合分": 100},
    ])
    a, b, report = split_pools(candidates, top_a=15, top_b=2, regime_max_slots=0)
    assert a.empty
    assert b["ts_code"].tolist() == ["strict-high", "strict-mid"]
    assert b["筛选层级"].tolist() == ["strict", "strict"]
    assert b["池"].tolist() == ["B", "B"]
    assert b["观察原因"].str.contains("仅作观察").all()
    assert report["qualified_strict"] == report["withheld_strict"] == 3
    assert report["withheld_strict_displayed"] == 2
    assert report["withheld_strict_not_displayed"] == 1
    assert report["a_slots"] == 0 and report["b_available_count"] == 4

    a, b, report = split_pools(candidates, top_a=1, top_b=30)
    assert a["ts_code"].tolist() == ["strict-high"]
    assert b["ts_code"].tolist() == ["strict-mid", "strict-low", "filler"]
    assert report["withheld_strict"] == 2
    assert report["withheld_strict_not_displayed"] == 0


@pytest.mark.parametrize("tier", ["missing", None, ""])
def test_missing_or_blank_tier_is_unknown_observation_not_strict(tier):
    row = {"ts_code": "fixture", "综合分": 100}
    if tier != "missing":
        row["筛选层级"] = tier
    a, b, report = split_pools(pd.DataFrame([row]))
    assert a.empty and b["筛选层级"].tolist() == ["unknown"]
    assert report["qualified_strict"] == 0


def test_two_positive_days_do_not_replace_a_complete_five_day_window():
    partial = moneyflow(WINDOW[:2])
    result = moneyflow_window_status(partial, expected_dates=WINDOW, expected_as_of=WINDOW[-1])
    assert result["complete"] is False
    assert result["observed_days"] == 2
    assert result["missing_dates"] == WINDOW[2:]
    assert fund_flow_quality_ok(partial, expected_dates=WINDOW, expected_as_of=WINDOW[-1]) == (False, 0)


@pytest.mark.parametrize("kind", ["duplicate", "null", "infinity", "missing_denominator", "bad_denominator"])
def test_count_alone_cannot_certify_fund_rows(kind):
    rows = moneyflow()
    if kind == "duplicate":
        rows.loc[4, "trade_date"] = WINDOW[0]
    elif kind == "null":
        rows.loc[2, "net_mf_amount"] = None
    elif kind == "infinity":
        rows.loc[2, "net_mf_amount"] = float("inf")
    elif kind == "missing_denominator":
        rows = rows.drop(columns="amount")
    else:
        rows.loc[2, "amount"] = -1
    result = moneyflow_window_status(rows, expected_dates=WINDOW, expected_as_of=WINDOW[-1])
    assert result["complete"] is False
    selected = moneyflow_rows_for_window(rows, result)
    assert len(selected) < 5


def test_complete_moneyflow_preserves_positive_day_threshold_and_excludes_other_dates():
    rows = moneyflow()
    assert fund_flow_quality_ok(rows, expected_dates=WINDOW, expected_as_of=WINDOW[-1]) == (True, 5)
    rows["net_mf_amount"] = [-1., -1., -1., -1., 1.]
    rows = pd.concat([rows, moneyflow(["20260914", "20260915"])], ignore_index=True)
    assert fund_flow_quality_ok(rows, expected_dates=WINDOW, expected_as_of=WINDOW[-1]) == (False, 1)
    assert fund_flow_quality_ok(rows) == (False, 0)  # No self-derived freshness reference.


def test_real_evaluator_control_flow_downgrades_partial_window_before_pool_split(monkeypatch):
    """Call the real evaluator, replacing only unrelated score/config dependencies."""
    import scoring
    import strategy_store
    from ab_screener.screener import evaluator

    monkeypatch.setattr(scoring, "build_master_score", lambda *a, **kw: (
        70., {"信号强度分": 80., "资金流分": 70., "基本面分": 60.},
    ))
    monkeypatch.setattr(strategy_store, "active_weights", dict)
    monkeypatch.setattr(evaluator, "FUND_FLOW_DAYS", 5)
    monkeypatch.setattr(evaluator, "FUND_POSITIVE_DAYS_MIN", 2)
    monkeypatch.setattr(evaluator, "fundamental_filter_passes", lambda row: (True, []))
    monkeypatch.setattr(evaluator, "calc_fund_flow_strength", lambda frame: (20., 70., .01))
    monkeypatch.setattr(evaluator, "fund_positive_days", len)
    monkeypatch.setattr(evaluator, "breakout_freshness_bonus", lambda *a, **kw: 0.)
    monkeypatch.setattr(evaluator, "match_themes", lambda *a: [])
    code = "000001.SZ"
    metadata = pd.DataFrame([{"ts_code": code, "name": "fixture", "industry": "银行", "close": 10., "pe": 10., "pb": 1., "total_mv": 1_000_000, "list_date": "20000101"}])
    signal = {code: {"is_breakout": True, "box_days": 40, "box_amp": .15, "breakout_date": "20260911", "reasons": ["fixture"]}}
    common = {"codes": [code], "sig_by_code": signal, "basic_latest": metadata, "fund_min_ratio": 0,
              "latest_date": WINDOW[-1], "trade_dates": WINDOW, "expected_fund_dates": WINDOW}
    partial = evaluator._score_codes(mf_by_code={code: moneyflow(WINDOW[:2])}, **common)
    assert partial[0]["筛选层级"] == "data_incomplete"
    assert partial[0]["资金缺失日期"] == ",".join(WINDOW[2:])
    assert partial[0]["主力净流入(万)"] is None
    assert "仅作观察" in partial[0]["入选理由"]
    a, b, _ = split_pools(pd.DataFrame(partial))
    assert a.empty and len(b) == 1
    complete = evaluator._score_codes(mf_by_code={code: moneyflow()}, **common)
    assert complete[0]["筛选层级"] == "strict"
    assert complete[0]["主力净流入(万)"] == 20
    assert complete[0]["fund_window"]["complete"] is True
