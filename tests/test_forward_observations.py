"""Prospective sidecar protocol: isolated SQLite fixtures only."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ab_screener.api.deps import get_db_path
from ab_screener.api.routers.forward_observations import router
from ab_screener.application import forward_observations as forward
from ab_screener.application import scan_publication
from ab_screener.domain.data_point import content_hash_for

TZ = ZoneInfo("Asia/Shanghai")
ENABLED = datetime(2026, 9, 13, 17, tzinfo=TZ)
CAPTURED = datetime(2026, 9, 13, 18, tzinfo=TZ)
SEMANTICS = {"target_count": 20, "box_ladder_days": [125, 105, 84, 63, 42, 20], "build_watch": True}


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def factor_row(day, factor, available_at, *, revision=1, source="tushare", code="000001.SZ"):
    payload = {"adj_factor": factor}
    return code, day, revision, available_at, source, content_hash_for(payload), json.dumps(payload)


@pytest.fixture
def market(tmp_path, monkeypatch):
    database = tmp_path / "market.db"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE trade_cal(cal_date TEXT PRIMARY KEY,is_open INTEGER,source TEXT)")
        cursor = date(2026, 9, 11)
        while cursor <= date(2026, 11, 1):
            conn.execute("INSERT INTO trade_cal VALUES (?,?,?)",
                         (cursor.strftime("%Y%m%d"), int(cursor.weekday() < 5), "tushare"))
            cursor += timedelta(days=1)
        conn.execute("CREATE TABLE daily(ts_code TEXT,trade_date TEXT,close REAL,vol REAL,PRIMARY KEY(ts_code,trade_date))")
        conn.execute("CREATE TABLE adj_factor_history(ts_code TEXT,trade_date TEXT,revision INTEGER,available_at TEXT,source TEXT,content_hash TEXT,payload_json TEXT)")
        conn.execute("CREATE TABLE scan_runs(run_id TEXT,task_id TEXT,status TEXT,created_at TEXT,as_of TEXT)")
        conn.execute("CREATE TABLE scan_jobs(task_id TEXT,started_at TEXT)")
        for code in ("000001.SZ", "000002.SZ", "000003.SZ"):
            conn.executemany("INSERT INTO daily VALUES (?,?,?,1000)",
                             [(code, "20260911", 10.0), (code, "20260914", 11.0)])

    def candidate(code="000001.SZ", group="A"):
        pool = "A" if group == "A" else "B"
        return {"ts_code": code, "trade_date": "20260911", "name": code,
                "pool": pool, "tier": "strict" if pool == "A" else "relaxed",
                "observation_group": group, "reasons": f"[池{pool}|strict]", "price": 10}

    publications = {}

    def publish(run_id="run-1", rows=None, **changes):
        result = {"run_id": run_id, "as_of": "20260911", "task_id": run_id,
                  "started_at": "2026-09-13T17:30:00+08:00", "completed_at": "2026-09-13T17:50:00+08:00",
                  "verified": True, "qualified_verified": True, "state": "READY", "publication_version": 2,
                  "config_hash": "frozen-config", "entry_hash": "entry", "dataset_version": "dataset",
                  "result_hash": run_id, "code_version": "code", "regime": {},
                  "qualification": {"version": 1, "hash": "qualified-hash", "rules_snapshot": {"version": 1, "semantics": SEMANTICS}},
                  "freshness": {"calendar_verified": True, "expected_as_of": "20260911", "historical": False},
                  "candidates": [candidate()] if rows is None else rows, **changes}
        publications[run_id] = result
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("INSERT INTO scan_runs VALUES (?,?,'SUCCEEDED',?,?)", (run_id, run_id, result["completed_at"], result["as_of"]))
            conn.execute("INSERT INTO scan_jobs VALUES (?,?)", (run_id, result["started_at"]))
        return result

    def read(db, run_id, *, stage):
        assert stage == "qualified" and str(db) == str(database)
        return copy.deepcopy(publications.get(run_id))

    monkeypatch.setattr(scan_publication, "read_scan_publication", read)
    return database, publish, candidate


def test_disabled_status_and_capture_never_create_sidecar(market):
    database, publish, _ = market
    publish()
    before = _hash(database)
    assert forward.forward_status(database)["enabled"] is False
    assert forward.capture_scan_publication(database, "run-1")["status"] == "DISABLED"
    assert not forward.sidecar_path(database).exists()
    assert _hash(database) == before


def test_enable_freezes_server_protocol_and_is_idempotent(market):
    database, _, _ = market
    before = _hash(database)
    first = forward.enable_forward_observations(database, now=ENABLED)
    second = forward.enable_forward_observations(database, now=CAPTURED)
    assert first["protocol"] == second["protocol"]
    assert first["protocol"]["horizons"] == [1, 5, 10, 20]
    assert first["protocol"]["metric"] == "price_observation"
    assert _hash(database) == before


def test_complete_groups_zero_runs_primary_secondary_and_snapshot_immutability(market):
    database, publish, candidate = market
    forward.enable_forward_observations(database, now=ENABLED)
    rows = [candidate(), candidate("000002.SZ", "B"), candidate("000003.SZ", "DATA_INCOMPLETE")]
    publication = publish(rows=rows, state="DATA_BLOCKED")
    before = _hash(database)
    first = forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    assert first["counts"] == {"A": 1, "B": 1, "DATA_INCOMPLETE": 1, "total": 3}
    assert first["role"] == "PRIMARY"
    publication["candidates"][0]["name"] = "mutated outside capture"
    same = forward.capture_scan_publication(database, "run-1", now=CAPTURED + timedelta(days=2))
    assert same["status"] == "ALREADY_CAPTURED" and same["candidate_hash"] == first["candidate_hash"]
    assert _hash(database) == before
    publish("zero", rows=[])
    zero = forward.capture_scan_publication(database, "zero", now=CAPTURED)
    assert zero["role"] == "SECONDARY" and zero["counts"]["total"] == 0
    assert forward.forward_history(database)["total"] == 2
    with forward._read(forward.sidecar_path(database)) as side:
        saved = json.loads(side.execute("SELECT payload_json FROM forward_candidates WHERE ts_code='000001.SZ'").fetchone()[0])
        assert saved["name"] == "000001.SZ"


@pytest.mark.parametrize("changes,reason", [
    ({"started_at": "2026-09-13T16:59:59+08:00"}, "STARTED_BEFORE_ENABLE"),
    ({"started_at": "2026-09-13T17:00:00+08:00"}, "STARTED_BEFORE_ENABLE"),
    ({"started_at": None}, "TIMESTAMP_UNVERIFIED"),
    ({"qualified_verified": False}, "QUALIFIED_PUBLICATION_REQUIRED"),
    ({"state": "HISTORICAL"}, "HISTORICAL_OR_UNVERIFIED"),
    ({"state": "LEGACY_UNVERIFIED", "verified": False}, "QUALIFIED_PUBLICATION_REQUIRED"),
    ({"entry_hash": ""}, "IDENTITY_INCOMPLETE"),
])
def test_old_unverified_or_incomplete_publication_cannot_be_backfilled(market, changes, reason):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish(**changes)
    result = forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    assert result["captured"] is False and result["reason"] == reason
    assert forward.forward_history(database)["total"] == 0
    status = forward.forward_status(database)
    assert status["last_error"]["reason"] == reason
    assert status["uncaptured_runs"] == ([] if reason in {"STARTED_BEFORE_ENABLE", "TIMESTAMP_UNVERIFIED"} else ["run-1"])


@pytest.mark.parametrize("clock", [datetime(2026, 9, 14, 9, 15, tzinfo=TZ), datetime(2026, 9, 14, 9, 29, tzinfo=TZ), datetime(2026, 9, 15, 18, tzinfo=TZ)])
def test_late_capture_rejected_even_when_scan_was_published_on_time(market, clock):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    result = forward.capture_scan_publication(database, "run-1", now=clock)
    assert result["reason"] == "LATE_CAPTURE"


@pytest.mark.parametrize("corruption", ["DELETE FROM trade_cal WHERE cal_date='20260912'",
                                        "UPDATE trade_cal SET source='local_infer' WHERE cal_date='20260912'"])
def test_calendar_closed_dates_and_independent_provenance_required(market, corruption):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(corruption)
    result = forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    assert result["reason"] == "NEXT_OPEN_UNVERIFIED"


def test_horizon_is_exchange_sessions_and_only_matures_after_close(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    before = _hash(database)
    forward.refresh_forward_observations(database, now=datetime(2026, 9, 14, 15, 59, tzinfo=TZ))
    waiting = forward.forward_results(database, horizon=1)["items"][0]
    assert waiting["status"] == "WAITING_HORIZON" and waiting["raw_return"] is None
    assert waiting["target_date"] == "20260914"  # Weekend is excluded.
    forward.refresh_forward_observations(database, now=datetime(2026, 9, 14, 16, tzinfo=TZ))
    result = forward.forward_results(database, horizon=1)["items"][0]
    assert result["status"] == "RAW_ONLY" and result["raw_return"] == pytest.approx(0.1)
    assert result["adjusted_return"] is None and result["base_factor"]["factor"] is None
    assert result["revision"] == 2
    assert _hash(database) == before


def test_missing_target_data_stays_null_and_late_data_adds_revision(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("DELETE FROM daily WHERE trade_date='20260914'")
    clock = datetime(2026, 9, 14, 18, tzinfo=TZ)
    forward.refresh_forward_observations(database, now=clock)
    missing = forward.forward_results(database, horizon=1)["items"][0]
    assert missing["status"] == "TARGET_QUOTE_MISSING" and missing["raw_return"] is None
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("INSERT INTO daily VALUES ('000001.SZ','20260914',12,1000)")
    forward.refresh_forward_observations(database, now=clock + timedelta(hours=1))
    revisions = forward.forward_results(database, horizon=1, all_revisions=True)["items"]
    assert [row["revision"] for row in revisions] == [2, 1]
    assert revisions[0]["raw_return"] == pytest.approx(0.2) and revisions[1]["raw_return"] is None
    unchanged = forward.refresh_forward_observations(database, now=clock + timedelta(hours=2))
    assert unchanged["appended"] == 0


def test_exact_date_factors_and_available_at_required_for_adjusted_observation(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    clock = datetime(2026, 9, 14, 18, tzinfo=TZ)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executemany("INSERT INTO adj_factor_history VALUES (?,?,?,?,?,?,?)", [
            factor_row("20260818", 1, "2026-08-18T18:00:00+08:00"),
            factor_row("20260911", 1, "2026-09-11T18:00:00+08:00"),
            factor_row("20260914", 2, "2026-09-14T19:00:00+08:00"),
        ])
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    forward.refresh_forward_observations(database, now=clock)
    before = forward.forward_results(database, horizon=1)["items"][0]
    assert before["status"] == "RAW_ONLY" and before["adjusted_return"] is None
    forward.refresh_forward_observations(database, now=clock + timedelta(hours=2))
    after = forward.forward_results(database, horizon=1)["items"][0]
    assert after["status"] == "OBSERVED" and after["adjusted_return"] == pytest.approx(1.2)
    assert after["revision"] == 2


def test_sidecar_rejects_updates_and_failed_capture_is_visible_without_main_mutation(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    before = _hash(database)
    with forward._write(forward.sidecar_path(database)) as side:
        side.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON forward_candidates BEGIN SELECT RAISE(ABORT,'capture disk fault'); END")
        side.commit()
    result = forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    assert result["status"] == "REJECTED"
    assert forward.forward_history(database)["total"] == 0
    assert forward.forward_status(database)["uncaptured_runs"] == ["run-1"]
    assert _hash(database) == before
    with forward._write(forward.sidecar_path(database)) as side, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        side.execute("DELETE FROM forward_protocol")


def test_api_contract_uses_local_sidecar_and_rejects_unknown_horizon(market, monkeypatch):
    database, publish, _ = market
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_path] = lambda: str(database)
    real_clock = forward._clock
    monkeypatch.setattr(forward, "_clock", lambda now=None: real_clock(now or ENABLED))
    with TestClient(app) as client:
        assert client.get("/api/forward/status").json()["enabled"] is False
        enabled = client.post("/api/forward/enable", json={"enabled_at": "2000-01-01T00:00:00+08:00"}).json()
        assert enabled["enabled"] is True and enabled["protocol"]["enabled_at"] == forward._stamp(ENABLED)
        publish()
        monkeypatch.setattr(forward, "_clock", lambda now=None: real_clock(now or CAPTURED))
        assert client.post("/api/forward/capture", json={"run_id": "run-1"}).json()["captured"] is True
        assert client.post("/api/forward/refresh", json={}).status_code == 200
        assert client.get("/api/forward/history").json()["total"] == 1
        assert client.get("/api/forward/results", params={"horizon": 1}).json()["total"] == 1
        assert client.get("/api/forward/results", params={"horizon": 2}).status_code == 422
        assert client.get("/api/forward/results", params={"limit": 0}).status_code == 422


@pytest.mark.parametrize("statement,status", [
    ("DELETE FROM daily WHERE trade_date='20260911'", "BASE_QUOTE_MISSING"),
    ("UPDATE daily SET close=0 WHERE trade_date='20260911'", "BASE_QUOTE_MISSING"),
    ("UPDATE daily SET vol=0 WHERE trade_date='20260914'", "TARGET_QUOTE_MISSING"),
])
def test_missing_base_invalid_price_or_suspension_is_null(market, statement, status):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(statement)
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    forward.refresh_forward_observations(database, now=datetime(2026, 9, 14, 18, tzinfo=TZ))
    observed = forward.forward_results(database, horizon=1)["items"][0]
    assert observed["status"] == status
    assert observed["raw_return"] is None and observed["adjusted_return"] is None


def test_all_horizons_follow_authoritative_holiday_calendar(market):
    database, publish, _ = market
    with closing(sqlite3.connect(database)) as conn, conn:
        # A fixture exchange holiday: still present, independently marked closed.
        conn.execute("UPDATE trade_cal SET is_open=0 WHERE cal_date='20260918'")
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    forward.refresh_forward_observations(database, now=CAPTURED)
    rows = forward.forward_results(database)["items"]
    assert {row["horizon"]: row["target_date"] for row in rows} == {
        1: "20260914", 5: "20260921", 10: "20260928", 20: "20261012",
    }
    assert all(row["status"] == "WAITING_HORIZON" and row["raw_return"] is None for row in rows)


def test_missing_later_calendar_marks_only_unresolved_horizons_null(market):
    database, publish, _ = market
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("DELETE FROM trade_cal WHERE cal_date>'20260914'")
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    assert forward.capture_scan_publication(database, "run-1", now=CAPTURED)["captured"]
    forward.refresh_forward_observations(database, now=datetime(2026, 9, 14, 18, tzinfo=TZ))
    rows = {row["horizon"]: row for row in forward.forward_results(database)["items"]}
    assert rows[1]["status"] == "RAW_ONLY"
    for horizon in (5, 10, 20):
        assert rows[horizon]["status"] == "CALENDAR_UNVERIFIED"
        assert rows[horizon]["raw_return"] is None and rows[horizon]["target_date"] is None


@pytest.fixture
def real_publication(tmp_path, monkeypatch):
    from ab_screener.application import scan_audit, scan_jobs
    from ab_screener.domain.profile import default_profile
    from local_store import LocalStore

    store = LocalStore(tmp_path / "real-publication.db")
    with closing(sqlite3.connect(store.db_path)) as conn, conn:
        for day, opened in (("20260911", 1), ("20260912", 0), ("20260913", 0), ("20260914", 1)):
            conn.execute("INSERT INTO trade_cal(cal_date,is_open,source,updated_at) VALUES (?,?,?,?)",
                         (day, opened, "tushare", "2026-09-13T16:00:00+08:00"))
    forward.enable_forward_observations(store.db_path, now=ENABLED)
    monkeypatch.setattr(scan_jobs, "_now", lambda: "2026-09-13T17:30:00+08:00")

    class PublishedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 13, 17, 50, tzinfo=TZ).astimezone(tz or TZ)

    monkeypatch.setattr(scan_audit, "datetime", PublishedClock)
    actual_clock = forward._clock
    monkeypatch.setattr(forward, "_clock", lambda now=None: actual_clock(now or CAPTURED))
    profile = default_profile()

    def publish(run_id="real", *, zero=False):
        rows = [] if zero else [
            {"ts_code": "000001.SZ", "name": "完整 A", "trade_date": "20260911", "total_score": 90,
             "pool": "A", "qualified_pool": "A", "tier": "strict", "observation_group": "A",
             "reasons": "[池A|strict|] evidence", "fund_window": {"complete": True}},
            {"ts_code": "000002.SZ", "name": "完整 B", "trade_date": "20260911", "total_score": 80,
             "pool": "B", "qualified_pool": "B", "tier": "relaxed", "observation_group": "B",
             "reasons": "[池B|relaxed|] evidence", "fund_window": {"complete": True}},
            {"ts_code": "000003.SZ", "name": "数据不足", "trade_date": "20260911", "total_score": 70,
             "pool": "B", "qualified_pool": "B", "tier": "data_incomplete", "observation_group": "DATA_INCOMPLETE",
             "reasons": "[池B|data_incomplete|] evidence", "fund_window": {"complete": False}},
        ]
        for row in rows:
            row.update(price=10.0, mv_yi=30.0, pe=20.0, pb=2.0, turnover=3.0, quote_as_of="20260911")
        final = rows[:1]
        scan_jobs.ScanJobStore(store.db_path).reserve_running(run_id, top_n=1, days=160)
        succeeded = scan_audit.complete_scan_run(
            store.db_path, run_id=run_id, task_id=run_id, as_of="20260911", days=160,
            result={"scan_candidates": final, "qualified_candidates": rows,
                    "qualification_report": scan_audit.build_qualification_report(rows, {"version": 1, "semantics": SEMANTICS}),
                    "freshness": {"can_publish_a": True, "calendar_verified": True, "historical": False,
                                  "expected_as_of": "20260911"}, "regime": {"allow_new_entries": True}},
            count_a=len(final), count_b=0, strategy_snapshot=profile.to_canonical_dict(),
            config_hash=profile.config_hash(), code_version="fixture-code", research_mode="daily_research",
        )
        return succeeded

    return store.db_path, publish


def test_real_commit_hook_captures_complete_qualified_list_and_zero_result(real_publication):
    database, publish = real_publication
    assert publish()
    top = scan_publication.read_scan_publication(database, "real")
    assert len(top["candidates"]) == 1
    qualified = scan_publication.read_scan_publication(database, "real", stage="qualified")
    assert qualified["qualified_verified"] and len(qualified["candidates"]) == 3
    assert qualified["started_at"] == "2026-09-13T17:30:00+08:00"
    captured = forward.forward_history(database)["items"][0]
    assert captured["counts"] == {"A": 1, "B": 1, "DATA_INCOMPLETE": 1, "total": 3}
    assert captured["qualification"]["hash"] == qualified["qualification"]["hash"]
    assert publish("real-zero", zero=True)
    history = forward.forward_history(database)
    assert history["total"] == 2
    assert next(row for row in history["items"] if row["run_id"] == "real-zero")["counts"]["total"] == 0


def test_real_scan_commit_survives_sidecar_io_failure_and_status_exposes_absence(real_publication, monkeypatch):
    database, publish = real_publication

    def disk_failure(*args, **kwargs):
        raise OSError("fixture sidecar disk unavailable")

    monkeypatch.setattr(forward, "_write", disk_failure)
    assert publish()
    publication = scan_publication.read_scan_publication(database, "real", stage="qualified")
    assert publication["qualified_verified"]
    status = forward.forward_status(database)
    assert status["counts"]["captures"] == 0 and status["uncaptured_runs"] == ["real"]


def test_core_app_registers_forward_observation_routes():
    from ab_screener.api.app_factory import CORE_V2_ROUTERS

    assert router in CORE_V2_ROUTERS
    app = FastAPI()
    app.include_router(router)
    assert {"/api/forward/" + action for action in ("enable", "status", "capture", "refresh", "history", "results")} <= set(app.openapi()["paths"])


def test_primary_follows_publication_order_even_if_captures_arrive_reversed(market, monkeypatch):
    database, publish, _ = market
    monkeypatch.setattr(forward, "_clock", lambda now=None: now or CAPTURED)
    forward.enable_forward_observations(database, now=ENABLED)
    publish("first")
    publish("second")  # Equal timestamp: immutable rowid is the stable tie-break.
    second = forward.capture_scan_publication(database, "second", now=CAPTURED)
    assert second["role"] == "SECONDARY" and second["primary_run_id"] == "first"
    assert forward.forward_status(database)["missing_primary_runs"] == ["first"]
    first = forward.capture_scan_publication(database, "first", now=CAPTURED)
    assert first["role"] == "PRIMARY" and first["primary_run_id"] == "first"
    assert first["cohort_key"] == second["cohort_key"]
    assert forward.forward_status(database)["missing_primary_runs"] == []


def test_primary_capture_failure_never_promotes_later_publication(market, monkeypatch):
    database, publish, _ = market
    monkeypatch.setattr(forward, "_clock", lambda now=None: now or CAPTURED)
    forward.enable_forward_observations(database, now=ENABLED)
    publish("first")
    with forward._write(forward.sidecar_path(database)) as side:
        side.execute("CREATE TRIGGER first_io_failure BEFORE INSERT ON forward_candidates WHEN NEW.run_id='first' "
                     "BEGIN SELECT RAISE(ABORT,'first observation failed'); END")
        side.commit()
    assert forward.capture_scan_publication(database, "first", now=CAPTURED)["status"] == "REJECTED"
    publish("second")
    second = forward.capture_scan_publication(database, "second", now=CAPTURED)
    assert second["role"] == "SECONDARY" and second["primary_run_id"] == "first"
    assert forward.forward_status(database)["counts"]["primary"] == 0
    assert forward.forward_status(database)["missing_primary_runs"] == ["first"]
    late = datetime(2026, 9, 14, 9, 15, tzinfo=TZ)
    assert forward.capture_scan_publication(database, "first", now=late)["reason"] == "LATE_CAPTURE"
    assert forward.forward_history(database)["items"][0]["role"] == "SECONDARY"


def test_exit_provenance_top_and_ladder_outcomes_do_not_create_new_primary(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish("original")
    first = forward.capture_scan_publication(database, "original", now=CAPTURED)
    publish("display-change", config_hash="different-exit-top-provenance-config", code_version="other-build",
            qualification={"version": 1, "hash": "different-full-evidence", "rules_snapshot": {
                "semantics": SEMANTICS, "version": "other-label", "top_n": 3, "source": "research",
                "box_ladder": {"tried": [{"min_days": 20, "count": 7}], "kept": 7}}})
    second = forward.capture_scan_publication(database, "display-change", now=CAPTURED)
    assert second["role"] == "SECONDARY" and second["cohort_key"] == first["cohort_key"]
    assert second["config_hash"] != first["config_hash"]  # Full lineage remains frozen as evidence.
    publish("real-rule-change", qualification={"version": 1, "hash": "changed-real-rule",
            "rules_snapshot": {"semantics": {**SEMANTICS, "box_ladder_days": [20]}}})
    third = forward.capture_scan_publication(database, "real-rule-change", now=CAPTURED)
    assert third["role"] == "PRIMARY" and third["cohort_key"] != first["cohort_key"]


def test_capture_immediately_before_auction_is_allowed_and_missing_semantics_rejected(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    result = forward.capture_scan_publication(database, "run-1", now=datetime(2026, 9, 14, 9, 14, 59, tzinfo=TZ))
    assert result["captured"] and "09:15:00" in result["capture_deadline"]
    publish("no-semantics", qualification={"version": 1, "hash": "hash", "rules_snapshot": {"version": 1}})
    assert forward.capture_scan_publication(database, "no-semantics", now=CAPTURED)["reason"] == "QUALIFICATION_SEMANTICS_REQUIRED"


def test_existing_other_protocol_is_preserved_and_cannot_be_silently_reenabled(market):
    database, _, _ = market
    old = {"version": "old-observation-protocol", "market_database": str(database.resolve())}
    path = forward.sidecar_path(database)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE forward_protocol(singleton INTEGER PRIMARY KEY,payload_json TEXT)")
        conn.execute("INSERT INTO forward_protocol VALUES (1,?)", (json.dumps(old),))
    with pytest.raises(forward.ForwardObservationError, match="版本不同"):
        forward.enable_forward_observations(database, now=ENABLED)
    with forward._read(path) as side:
        assert forward._protocol(side) == old


@pytest.mark.parametrize("change,restated_status,restated_return", [
    ("UPDATE daily SET close=20 WHERE trade_date='20260911'", "RAW_ONLY", -0.45),
    ("DELETE FROM daily WHERE trade_date='20260911'", "BASE_QUOTE_MISSING", None),
])
def test_first_refresh_preserves_baseline_known_at_capture_after_source_revision(market, change, restated_status, restated_return):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    capture = forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    assert capture["captured"] and capture["baseline_hash"]
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(change)
    before = _hash(database)
    clock = datetime(2026, 9, 14, 18, tzinfo=TZ)
    assert forward.refresh_forward_observations(database, now=clock)["status"] == "REFRESHED"
    result = forward.forward_results(database, horizon=1)["items"][0]
    assert result["base_quote"]["close"] == 10.0 and result["raw_return"] == pytest.approx(0.1)
    assert result["observation_basis"] == "CAPTURED_BASELINE" and result["revision"] == 1
    assert result["baseline_captured_at"] == forward._stamp(CAPTURED)
    assert result["restated"]["basis"] == "LATEST_SOURCE_AT_REFRESH"
    assert result["restated"]["status"] == restated_status
    if restated_return is None:
        assert result["restated"]["raw_return"] is None
    else:
        assert result["restated"]["raw_return"] == pytest.approx(restated_return)
    assert result["lineage"]["previous_revision"] is None
    assert "BASE_SOURCE_RESTATED" in result["lineage"]["causes"]
    assert _hash(database) == before
    assert forward.refresh_forward_observations(database, now=clock + timedelta(hours=1))["appended"] == 0


def test_baseline_missing_at_capture_is_never_reconstructed_as_original_evidence(market):
    database, publish, _ = market
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("DELETE FROM daily WHERE trade_date='20260911'")
    forward.enable_forward_observations(database, now=ENABLED)
    publish()  # A frozen candidate price alone cannot certify a missing daily quote.
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("INSERT INTO daily VALUES ('000001.SZ','20260911',10,1000)")
    forward.refresh_forward_observations(database, now=datetime(2026, 9, 14, 18, tzinfo=TZ))
    result = forward.forward_results(database, horizon=1)["items"][0]
    assert result["base_quote"]["close"] is None and result["raw_return"] is None
    assert result["status"] == "BASE_QUOTE_MISSING"
    assert result["restated"]["raw_return"] == pytest.approx(0.1)


def test_late_factors_are_only_restated_and_revisions_keep_original_lineage(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executemany("INSERT INTO adj_factor_history VALUES (?,?,?,?,?,?,?)", [
            factor_row("20260911", 1, "2026-09-14T17:00:00+08:00"),
            factor_row("20260914", 2, "2026-09-14T17:00:00+08:00"),
        ])
    clock = datetime(2026, 9, 14, 18, tzinfo=TZ)
    forward.refresh_forward_observations(database, now=clock)
    first = forward.forward_results(database, horizon=1)["items"][0]
    assert first["base_factor"]["status"] == "MISSING"
    assert first["adjusted_return"] is None and first["status"] == "RAW_ONLY"
    assert first["restated"]["adjusted_return"] == pytest.approx(1.2)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("INSERT INTO adj_factor_history VALUES (?,?,?,?,?,?,?)",
                     factor_row("20260911", 2, "2026-09-14T19:00:00+08:00", revision=2))
    forward.refresh_forward_observations(database, now=clock + timedelta(hours=2))
    all_rows = forward.forward_results(database, horizon=1, all_revisions=True)["items"]
    latest = all_rows[0]
    assert latest["restated"]["adjusted_return"] == pytest.approx(0.1)
    assert latest["adjusted_return"] is None and latest["base_factor"] == first["base_factor"]
    assert latest["lineage"]["previous_revision"] == first["revision"]
    assert latest["lineage"]["previous_input_hash"] == first["input_hash"]
    assert latest["lineage"]["baseline_snapshot_hash"] == first["baseline_snapshot_hash"]
    assert all_rows[1] == first
    assert "BASE_SOURCE_RESTATED" in latest["lineage"]["causes"]


def test_factor_cutoff_uses_aware_instants_across_offsets(market):
    database, _, _ = market
    clock = datetime(2026, 9, 14, 18, tzinfo=TZ)
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executemany("INSERT INTO adj_factor_history VALUES (?,?,?,?,?,?,?)", [
            factor_row("20260914", 1, "2026-09-14T09:00:00+00:00", revision=1),
            factor_row("20260914", 2, "2026-09-14T11:00:00+00:00", revision=2),
        ])
        before = forward._factor(conn, "000001.SZ", "20260914", clock, {"adj_factor_history"})
        assert before["factor"] == 1 and before["status"] == "AVAILABLE"
        assert before["available_at"] == "2026-09-14T17:00:00.000000+08:00"
        after = forward._factor(conn, "000001.SZ", "20260914", clock + timedelta(hours=1), {"adj_factor_history"})
        assert after["factor"] == 2 and after["revision"] == 2


@pytest.mark.parametrize("field,value,status", [
    (4, "", "INVALID_SOURCE"), (4, "   ", "INVALID_SOURCE"),
    (5, "forged-hash", "INVALID_CONTENT_HASH"), (2, 0, "INVALID_REVISION"),
    (3, "2026-09-14T17:00:00", "INVALID_AVAILABLE_AT"),
    (6, "not-json", "INVALID_PAYLOAD"),
])
def test_factor_identity_faults_return_null_without_certifying_adjustment(market, field, value, status):
    database, _, _ = market
    row = list(factor_row("20260914", 2, "2026-09-14T17:00:00+08:00"))
    row[field] = value
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("INSERT INTO adj_factor_history VALUES (?,?,?,?,?,?,?)", row)
        result = forward._factor(conn, "000001.SZ", "20260914", datetime(2026, 9, 14, 18, tzinfo=TZ), {"adj_factor_history"})
    assert result["factor"] is None and result["status"] == status


def test_corrupt_newest_eligible_factor_does_not_silently_fall_back(market):
    database, _, _ = market
    corrupted = list(factor_row("20260914", 2, "2026-09-14T17:00:00+08:00", revision=2))
    corrupted[5] = "forged"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executemany("INSERT INTO adj_factor_history VALUES (?,?,?,?,?,?,?)", [
            factor_row("20260914", 1, "2026-09-14T16:00:00+08:00"), corrupted,
        ])
        result = forward._factor(conn, "000001.SZ", "20260914", datetime(2026, 9, 14, 18, tzinfo=TZ), {"adj_factor_history"})
    assert result["factor"] is None and result["status"] == "INVALID_CONTENT_HASH" and result["revision"] == 2


def test_existing_capture_and_read_apis_must_match_bound_market(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    other = database.with_name("another-market.db")
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(other)) as destination:
        source.backup(destination)
    refused = forward.capture_scan_publication(other, "run-1", now=CAPTURED)
    assert refused["captured"] is False and refused["reason"] == "SIDECAR_MARKET_MISMATCH"
    assert forward.capture_scan_publication(database, "run-1", now=CAPTURED)["status"] == "ALREADY_CAPTURED"
    for read in (forward.forward_status, forward.forward_history, forward.forward_results):
        with pytest.raises(forward.ForwardObservationError) as exc:
            read(other)
        assert exc.value.code == "SIDECAR_MARKET_MISMATCH"


def test_baseline_snapshot_is_append_only_and_integrity_checked(market):
    database, publish, _ = market
    forward.enable_forward_observations(database, now=ENABLED)
    publish()
    forward.capture_scan_publication(database, "run-1", now=CAPTURED)
    with forward._write(forward.sidecar_path(database)) as side:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            side.execute("UPDATE forward_baselines SET payload_json='{}'")
        # Deliberate fixture corruption after removing the guard must still fail closed.
        side.execute("DROP TRIGGER forward_baselines_no_update")
        side.execute("UPDATE forward_baselines SET payload_json='{}'")
        side.commit()
    result = forward.refresh_forward_observations(database, now=datetime(2026, 9, 14, 18, tzinfo=TZ))
    assert result["status"] == "FAILED" and result["reason"] == "BASELINE_INTEGRITY_ERROR"
    assert forward.forward_results(database)["total"] == 0
