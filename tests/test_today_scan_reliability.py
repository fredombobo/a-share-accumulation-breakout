"""Daily completion and scan start must be backed by current, durable evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ab_screener.application.scan_jobs import ScanJobStore
from ab_screener.application.today_guide import build_today_guide
from local_store import LocalStore

_NOW = datetime(2026, 8, 7, 18, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def current_db(tmp_path: Path) -> Path:
    path = tmp_path / "daily.db"
    LocalStore(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO daily(ts_code,trade_date,close) VALUES('000001.SZ','20260807',10)")
        conn.execute("INSERT INTO daily_basic(ts_code,trade_date) VALUES('000001.SZ','20260807')")
        conn.execute("INSERT INTO moneyflow(ts_code,trade_date) VALUES('000001.SZ','20260807')")
        dates = [_NOW - timedelta(days=n) for n in range(16)]
        conn.executemany("INSERT INTO trade_cal(cal_date,is_open,source,updated_at) VALUES(?,?,?,?)",
                         [(day.strftime("%Y%m%d"), int(day.weekday() < 5), "tushare", "test") for day in dates])
    return path


def _publication(path: Path, *, run_id="new", as_of="20260807", version=2,
                 state="READY", created_at="2026-08-07T18:00:00+08:00") -> None:
    snapshot = {"_publication": {"version": version, "state": state,
                                "counts": {"A": 0, "B": 0}, "freshness": {"can_publish_a": True}}}
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO scan_runs(run_id,task_id,as_of,strategy_snapshot_json,config_hash,git_sha,"
            "dataset_version,input_hash,result_hash,research_mode,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'SUCCEEDED',?)",
            (run_id, run_id, as_of, json.dumps(snapshot), "cfg", "code", "data", "input", "result", "daily", created_at),
        )


@pytest.mark.parametrize("table", ["moneyflow", "daily_basic"])
def test_old_required_dataset_prevents_completion(current_db: Path, table: str) -> None:
    _publication(current_db)
    with sqlite3.connect(current_db) as conn:
        conn.execute(f"UPDATE {table} SET trade_date='20260806'")
    guide = build_today_guide(current_db, now=_NOW)
    assert guide["next_action"] == "SYNC_DATA"
    expected = "MONEYFLOW_DATA_STALE" if table == "moneyflow" else "DAILY_BASIC_DATA_STALE"
    assert expected in guide["blocker_codes"]


@pytest.mark.parametrize("change", ["missing", "inferred", "hole"])
def test_unknown_calendar_never_uses_latest_quote_as_expected(current_db: Path, change: str) -> None:
    _publication(current_db)
    with sqlite3.connect(current_db) as conn:
        if change == "missing":
            conn.execute("DROP TABLE trade_cal")
        elif change == "inferred":
            conn.execute("UPDATE trade_cal SET source='local_infer'")
        else:
            conn.execute("DELETE FROM trade_cal WHERE cal_date='20260805'")
    guide = build_today_guide(current_db, now=_NOW)
    assert guide["next_action"] == "SYNC_DATA"
    assert guide["freshness"]["calendar_verified"] is False


@pytest.mark.parametrize("version,state,as_of", [
    (1, "READY", "20260807"), (2, "DATA_BLOCKED", "20260807"),
    (2, "HISTORICAL", "20260807"), (2, "READY", "20260806"),
])
def test_unverified_blocked_or_old_publication_recommends_scan(current_db: Path, version, state, as_of) -> None:
    _publication(current_db, version=version, state=state, as_of=as_of)
    assert build_today_guide(current_db, now=_NOW)["next_action"] == "RUN_SCAN"


def test_latest_publication_wins_including_empty_list(current_db: Path) -> None:
    _publication(current_db, run_id="earlier", state="DATA_BLOCKED", created_at="2026-08-07T17:00:00+08:00")
    _publication(current_db)
    before = hashlib.sha256(current_db.read_bytes()).hexdigest()
    guide = build_today_guide(current_db, now=_NOW)
    assert guide["next_action"] == "DAILY_COMPLETE"
    assert guide["scan_run_id"] == "new"
    assert guide["freshness"]["reference_now"] == _NOW.isoformat(timespec="seconds")
    assert hashlib.sha256(current_db.read_bytes()).hexdigest() == before


def test_latest_unverified_result_does_not_fall_back_to_earlier_ready(current_db: Path) -> None:
    _publication(current_db, run_id="earlier", created_at="2026-08-07T17:00:00+08:00")
    _publication(current_db, version=1)
    assert build_today_guide(current_db, now=_NOW)["next_action"] == "RUN_SCAN"


def test_guide_missing_database_does_not_create_it(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    assert build_today_guide(db, now=_NOW)["next_action"] == "SYNC_DATA"
    assert not db.exists()


def test_active_scan_remains_the_only_next_action(current_db: Path) -> None:
    ScanJobStore(current_db).reserve_running("working", top_n=15, days=160)
    guide = build_today_guide(current_db, now=_NOW)
    assert guide["next_action"] == "WAIT_SCAN"
    assert guide["task_id"] == "working"


@pytest.fixture
def scan_api(current_db: Path, tmp_path: Path, monkeypatch):
    from ab_screener.api.routers import legacy_scan

    store = LocalStore(current_db)
    monkeypatch.setattr(legacy_scan, "_store", store)
    monkeypatch.setattr(legacy_scan, "_PARENT", tmp_path)
    monkeypatch.setattr(legacy_scan, "_SCAN_TASKS", {})
    monkeypatch.setattr(legacy_scan, "_SCAN_CANCEL_EVENTS", {})
    monkeypatch.setattr(legacy_scan, "_SCAN_LOCK", threading.Lock())
    starts = []

    class ReservedThread:
        def __init__(self, *, target, args, daemon):
            self.task_id = args[0]

        def start(self):
            # Assert actual durable reservation precedes worker start.
            job = ScanJobStore(current_db).get(self.task_id)
            assert job and job["status"] == "RUNNING"
            assert self.task_id in legacy_scan._SCAN_TASKS
            starts.append(self.task_id)

    monkeypatch.setattr(legacy_scan, "threading", SimpleNamespace(Thread=ReservedThread, Event=threading.Event))
    app = FastAPI()
    app.include_router(legacy_scan.router)
    return legacy_scan, app, starts


def test_concurrent_http_starts_reserve_exactly_one_job(current_db: Path, scan_api, monkeypatch) -> None:
    scan, app, starts = scan_api
    barrier = threading.Barrier(2)

    def both_observe_idle():
        barrier.wait(timeout=10)

    monkeypatch.setattr(scan, "_running_task_id", both_observe_idle)

    def request():
        with TestClient(app) as client:
            response = client.post("/api/scan", json={"top": 15, "days": 160})
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(), range(2)))
    assert sorted(status for status, _ in results) == [200, 409]
    assert len(starts) == len(scan._SCAN_TASKS) == 1
    with sqlite3.connect(current_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0] == 1
        assert conn.execute("SELECT task_id FROM scan_jobs").fetchone()[0] == starts[0]


def test_durable_write_failure_starts_no_memory_task_or_thread(current_db: Path, scan_api) -> None:
    scan, app, starts = scan_api
    with sqlite3.connect(current_db) as conn:
        conn.execute("CREATE TRIGGER reject_scan BEFORE INSERT ON scan_jobs BEGIN SELECT RAISE(ABORT,'disk write rejected'); END")
    with TestClient(app) as client:
        response = client.post("/api/scan", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SCAN_RESERVATION_FAILED"
    assert starts == []
    assert scan._SCAN_TASKS == scan._SCAN_CANCEL_EVENTS == {}
    with sqlite3.connect(current_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0] == 0


def test_persisted_active_scan_blocks_after_server_memory_is_empty(current_db: Path, scan_api) -> None:
    scan, app, starts = scan_api
    ScanJobStore(current_db).reserve_running("other-server", top_n=15, days=160)
    with TestClient(app) as client:
        response = client.post("/api/scan", json={})
    assert response.status_code == 409
    assert "other-server" in response.json()["detail"]
    assert not starts and not scan._SCAN_TASKS


def test_thread_creation_failure_releases_durable_reservation(current_db: Path, scan_api, monkeypatch) -> None:
    scan, app, starts = scan_api

    def fail_thread(**kwargs):
        raise RuntimeError("no thread resources")

    monkeypatch.setattr(scan.threading, "Thread", fail_thread)
    with TestClient(app) as client:
        response = client.post("/api/scan", json={})
    assert response.status_code == 503
    assert not starts and not scan._SCAN_TASKS
    assert ScanJobStore(current_db).latest()["status"] == "FAILED"


@pytest.mark.parametrize("candidates", ["missing", None, {}])
def test_worker_rejects_child_without_owned_candidate_list(current_db: Path, scan_api, monkeypatch, candidates) -> None:
    from ab_screener.application import scan_audit, scan_spawn
    from ab_screener.domain.profile import default_profile

    scan, _, _ = scan_api
    profile = default_profile()
    frozen, fingerprint = profile.to_canonical_dict(), profile.config_hash()
    ScanJobStore(current_db).reserve_running("child-missing", top_n=15, days=160)
    scan._new_task(15, 160, requested_days=160, profile_snapshot=frozen,
                   config_hash=fingerprint, task_id="child-missing")
    audit_calls = []
    monkeypatch.setattr(scan_audit, "complete_scan_run", lambda *a, **kw: audit_calls.append(kw))

    def spawn(**kwargs):
        payload = {"status": "ok", "latest_date": "20260807", "strategy_profile": frozen,
                   "strategy_profile_hash": fingerprint, "count_a": 0, "count_b": 0}
        if candidates != "missing":
            payload["scan_candidates"] = candidates
        kwargs["result"].write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(pid=0, returncode=0, poll=lambda: 0)

    monkeypatch.setattr(scan_spawn, "spawn_scan_runner", spawn)
    scan._run_scan_worker("child-missing", 15, 160, frozen, fingerprint)
    assert audit_calls == []
    job = ScanJobStore(current_db).get("child-missing")
    assert job["status"] == "FAILED"
    assert "scan_candidates" in job["error_message"]


def test_reserved_job_has_current_process_creation_identity(current_db: Path) -> None:
    import os

    import psutil

    ScanJobStore(current_db).reserve_running("owned", top_n=15, days=160)
    identity = json.loads(ScanJobStore(current_db).get("owned")["worker_id"])
    assert identity == {"version": 1, "pid": os.getpid(), "create_time": psutil.Process().create_time()}


@pytest.mark.parametrize("process_state,expected", [
    ("alive", "ALIVE"), ("missing", "DEAD"), ("recycled", "DEAD"), ("denied", "UNKNOWN"),
])
def test_owner_check_uses_creation_time_and_fails_closed(monkeypatch, process_state, expected) -> None:
    from ab_screener.application import scan_jobs

    def process(pid):
        if process_state == "missing":
            raise scan_jobs.psutil.NoSuchProcess(pid)
        if process_state == "denied":
            raise scan_jobs.psutil.AccessDenied(pid)
        return SimpleNamespace(create_time=lambda: 13.0 if process_state == "recycled" else 12.0,
                               is_running=lambda: True)

    monkeypatch.setattr(scan_jobs.psutil, "Process", process)
    identity = json.dumps({"version": 1, "pid": 101, "create_time": 12.0})
    assert scan_jobs.scan_owner_state(identity) == expected
    assert scan_jobs.scan_owner_state(None) == "UNKNOWN"
    assert scan_jobs.scan_owner_state("legacy-worker") == "UNKNOWN"


@pytest.mark.parametrize("cancel", [False, True])
def test_confirmed_orphan_is_terminal_and_audited_without_requeue(current_db: Path, monkeypatch, cancel) -> None:
    from ab_screener.application import scan_jobs

    jobs = ScanJobStore(current_db)
    jobs.reserve_running("dead-owner", top_n=15, days=160)
    if cancel:
        jobs.request_cancel("dead-owner")
    monkeypatch.setattr(scan_jobs, "scan_owner_state", lambda identity: "DEAD")
    diagnostic = jobs.recover_dead_owners()["dead-owner"]
    row = jobs.get("dead-owner")
    assert row["status"] == ("CANCELLED" if cancel else "FAILED")
    assert row["error_code"] == "SCAN_OWNER_EXITED"
    recovery = json.loads(row["checkpoint_json"])["recovery"]
    assert recovery["policy"] == "confirmed-owner-dead-v1"
    assert recovery["owner"] == row["worker_id"]
    assert diagnostic["owner_state"] == "DEAD"
    assert jobs.recover_dead_owners() == {}


def test_old_heartbeat_is_not_proof_that_live_owner_died(current_db: Path) -> None:
    jobs = ScanJobStore(current_db)
    jobs.reserve_running("alive-owner", top_n=15, days=160)
    with sqlite3.connect(current_db) as conn:
        conn.execute("UPDATE scan_jobs SET heartbeat_at='2000-01-01T00:00:00+08:00'")
    assert jobs.recover_dead_owners() == {}
    assert jobs.get("alive-owner")["status"] == "RUNNING"


def test_status_and_explicit_cancel_handle_unknown_legacy_owner(current_db: Path, scan_api, monkeypatch) -> None:
    import scan_runtime

    scan, app, starts = scan_api
    jobs = ScanJobStore(current_db)
    jobs.upsert_running("legacy-owner", top_n=15, days=160)
    killed = []
    monkeypatch.setattr(scan_runtime, "kill_process_tree", lambda pid: killed.append(pid))
    with TestClient(app) as client:
        status = client.get("/api/scan/status", params={"task_id": "legacy-owner"})
        assert status.json()["status"] == "running"
        assert status.json()["recovery"]["code"] == "SCAN_OWNER_UNVERIFIED"
        blocked = client.post("/api/scan", json={})
        assert blocked.status_code == 409
        assert "身份无法核对" in blocked.json()["detail"]
        cancelled = client.post("/api/scan/legacy-owner/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert jobs.get("legacy-owner")["status"] == "CANCELLED"
    assert (scan._PARENT / "runtime/scan_legacy-owner.cancel").is_file()
    assert killed == starts == []


def test_status_recovers_dead_owner_before_restoring_progress(current_db: Path, scan_api, monkeypatch) -> None:
    from ab_screener.application import scan_jobs

    scan, app, _ = scan_api
    jobs = ScanJobStore(current_db)
    jobs.reserve_running("orphan", top_n=15, days=160)
    monkeypatch.setattr(scan_jobs, "scan_owner_state", lambda identity: "DEAD")
    with TestClient(app) as client:
        response = client.get("/api/scan/status", params={"task_id": "orphan"})
    assert response.json()["status"] == "error"
    assert response.json()["recovery"]["owner_state"] == "DEAD"
    assert response.json()["recovery"]["cancel_signal"] == "SENT"
    assert (scan._PARENT / "runtime/scan_orphan.cancel").read_text(encoding="utf-8") == "1"
    assert jobs.get("orphan")["status"] == "FAILED"


@pytest.mark.parametrize("unsafe_id", ["../escape", "nested/name", "nested\\name"])
def test_recovery_cancel_file_never_uses_uncontrolled_task_path(current_db: Path, scan_api, monkeypatch, unsafe_id) -> None:
    from ab_screener.application import scan_jobs

    scan, app, _ = scan_api
    ScanJobStore(current_db).reserve_running(unsafe_id, top_n=15, days=160)
    monkeypatch.setattr(scan_jobs, "scan_owner_state", lambda identity: "DEAD")
    with TestClient(app) as client:
        response = client.get("/api/scan/status", params={"task_id": unsafe_id})
    assert response.json()["recovery"]["cancel_signal"] == "UNSAFE_TASK_ID"
    assert list(scan._PARENT.rglob("*.cancel")) == []


def test_start_recovers_orphan_then_reserves_new_job(current_db: Path, scan_api, monkeypatch) -> None:
    from ab_screener.application import scan_jobs

    _, app, starts = scan_api
    jobs = ScanJobStore(current_db)
    jobs.reserve_running("old-orphan", top_n=15, days=160)
    monkeypatch.setattr(scan_jobs, "scan_owner_state", lambda identity: "DEAD")
    with TestClient(app) as client:
        response = client.post("/api/scan", json={})
    assert response.status_code == 200
    assert len(starts) == 1
    assert jobs.get("old-orphan")["status"] == "FAILED"
    assert jobs.get(starts[0])["status"] == "RUNNING"


def test_worker_persists_heartbeat_and_child_identity(current_db: Path, scan_api, monkeypatch) -> None:
    import itertools
    import time

    from ab_screener.application import scan_jobs, scan_spawn
    from ab_screener.domain.profile import default_profile

    scan, _, _ = scan_api
    profile = default_profile()
    frozen, fingerprint = profile.to_canonical_dict(), profile.config_hash()
    jobs = ScanJobStore(current_db)
    jobs.reserve_running("heartbeat", top_n=15, days=160)
    scan._new_task(15, 160, requested_days=160, profile_snapshot=frozen,
                   config_hash=fingerprint, task_id="heartbeat")
    child_identity = '{"version":1,"pid":123456,"create_time":12.0}'
    monkeypatch.setattr(scan_jobs, "scan_process_identity", lambda pid=None: child_identity)
    ticks = itertools.count(step=6)
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    pulses = []
    actual_heartbeat = ScanJobStore.heartbeat

    def heartbeat(store, task_id, checkpoint=None):
        pulses.append(dict(checkpoint or {}))
        actual_heartbeat(store, task_id, checkpoint)

    monkeypatch.setattr(ScanJobStore, "heartbeat", heartbeat)

    def spawn(**kwargs):
        kwargs["result"].write_text('{"status":"cancelled"}', encoding="utf-8")
        poll = iter([None, None, 0])
        return SimpleNamespace(pid=123456, returncode=0, poll=lambda: next(poll))

    monkeypatch.setattr(scan_spawn, "spawn_scan_runner", spawn)
    scan._run_scan_worker("heartbeat", 15, 160, frozen, fingerprint)
    assert len(pulses) >= 4  # includes periodic pulses without any child progress file
    assert any(pulse["worker_pid"] == 123456 and pulse["worker_identity"] == child_identity for pulse in pulses)
    row = jobs.get("heartbeat")
    assert row["status"] == "CANCELLED"
    assert row["heartbeat_at"]
    assert json.loads(row["checkpoint_json"])["worker_identity"] == child_identity
