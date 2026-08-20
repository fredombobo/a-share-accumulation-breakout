"""G1：扫描运行只读仓库（API 不再直连 sqlite）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.scan_run_repository import (
    ScanRunNotFound,
    ScanRunSchemaMissing,
    active_scan_worker,
    get_scan_run,
    list_scan_runs,
    schema_max_version,
)


def test_list_runs_missing_table_returns_empty(tmp_path: Path):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    assert list_scan_runs(db) == []


def test_list_and_get_scan_run(tmp_path: Path):
    db = tmp_path / "scan.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE scan_runs (
            run_id TEXT PRIMARY KEY, created_at TEXT, as_of TEXT
        );
        CREATE TABLE scan_run_candidates (
            run_id TEXT, stage TEXT, total_score REAL
        );
        INSERT INTO scan_runs VALUES ('r1', '2026-08-20', '20260819');
        INSERT INTO scan_run_candidates VALUES ('r1', 'strict', 80.0);
        """
    )
    conn.commit()
    conn.close()

    runs = list_scan_runs(db, limit=10)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "r1"

    payload = get_scan_run(db, "r1")
    assert payload["run"]["run_id"] == "r1"
    assert payload["funnel"][0]["stage"] == "strict"
    assert payload["funnel"][0]["n"] == 1

    with pytest.raises(ScanRunNotFound):
        get_scan_run(db, "missing")


def test_get_scan_run_schema_missing(tmp_path: Path):
    db = tmp_path / "noschema.db"
    sqlite3.connect(str(db)).close()
    with pytest.raises(ScanRunSchemaMissing):
        get_scan_run(db, "r1")


def test_schema_and_worker_meta(tmp_path: Path):
    db = tmp_path / "meta.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER);
        INSERT INTO schema_version VALUES (9);
        CREATE TABLE scan_jobs (
            task_id TEXT, status TEXT, worker_id TEXT,
            heartbeat_at TEXT, updated_at TEXT
        );
        INSERT INTO scan_jobs VALUES ('t1','RUNNING','w1','now','now');
        """
    )
    conn.commit()
    conn.close()
    assert schema_max_version(db) == 9
    hb = active_scan_worker(db)
    assert hb is not None
    assert hb["worker_id"] == "w1"
    assert hb["status"] == "RUNNING"
