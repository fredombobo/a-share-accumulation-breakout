from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ab_screener.application.daily_manifest import create_daily_manifest, get_daily_manifest
from ab_screener.data.migrations_v2 import run_v2_migrations
from local_store import LocalStore
from paper_trading.account import create_account
from paper_trading.migrations import run_migrations
from web import backend_app as backend
import ab_screener.api.routers.legacy_misc as legacy_misc


def _setup(db: Path, *, include_scan: bool = True) -> None:
    LocalStore(db_path=db)
    run_migrations(db)
    run_v2_migrations(db)
    create_account(db, 1_000_000)
    with sqlite3.connect(db) as conn:
        if include_scan:
            conn.execute(
                "INSERT INTO scan_runs(run_id,task_id,as_of,strategy_snapshot_json,config_hash,"
                "git_sha,dataset_version,input_hash,result_hash,research_mode,status,created_at) "
                "VALUES ('scan-1','task-1','20260807','{}','cfg-1','code-1','daily-fp-1',"
                "'input-1','result-1','full','SUCCEEDED','2026-08-07T16:00:00+08:00')"
            )
            conn.execute(
                "INSERT INTO scan_run_candidates(run_id,ts_code,stage,pool,tier,total_score,payload_json) "
                "VALUES ('scan-1','000001.SZ','final','A','strict',98.0,'{}')"
            )
        conn.execute(
            "INSERT INTO pt_signal_snapshot(trade_date,ts_code,pool,total_score,input_hash,"
            "available_at,source,tradeable) VALUES "
            "('20260807','000001.SZ','A',98.0,'sig-1','2026-08-07T16:05:00+08:00','scan_result',1)"
        )
        conn.execute(
            "INSERT INTO pt_cycle(cycle_id,run_date,phase,data_version,started_at,finished_at) "
            "VALUES ('CY-20260807','20260807','DONE','daily:20260807',"
            "'2026-08-07T16:15:00+08:00','2026-08-07T16:16:00+08:00')"
        )
        conn.execute(
            "INSERT INTO pt_reconciliation(run_date,result,diff_json,severity,status,checked_at) "
            "VALUES ('20260807','OK','[]','INFO','RESOLVED','2026-08-07T16:16:00+08:00')"
        )
        conn.execute(
            "INSERT INTO pt_daily_snapshot(account_id,trade_date,cash_fen,market_value_fen,"
            "total_asset_fen,positions_json) VALUES (1,'20260807',1000000,0,1000000,'[]')"
        )


def test_complete_manifest_is_deterministic_idempotent_and_linked(tmp_path: Path) -> None:
    db = tmp_path / "manifest.db"
    _setup(db)

    first = create_daily_manifest(db, "20260807")
    second = create_daily_manifest(db, "20260807")

    assert first["status"] == "COMPLETE"
    assert first["manifest_id"] == second["manifest_id"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["payload"]["scan"]["run_id"] == "scan-1"
    assert first["payload"]["signals"]["count"] == 1
    assert first["payload"]["paper"]["cycle_id"] == "CY-20260807"
    assert first["payload"]["reconciliation"]["result"] == "OK"
    assert get_daily_manifest(db, "20260807")["manifest_id"] == first["manifest_id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_run_manifests").fetchone()[0] == 1


def test_manifest_rows_are_append_only(tmp_path: Path) -> None:
    db = tmp_path / "immutable.db"
    _setup(db)
    manifest = create_daily_manifest(db, "20260807")

    with sqlite3.connect(db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE daily_run_manifests SET status='PARTIAL' WHERE manifest_id=?",
            (manifest["manifest_id"],),
        )
    with sqlite3.connect(db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "DELETE FROM daily_run_manifests WHERE manifest_id=?",
            (manifest["manifest_id"],),
        )


def test_missing_scan_is_recorded_as_partial_not_silently_complete(tmp_path: Path) -> None:
    db = tmp_path / "partial.db"
    _setup(db, include_scan=False)

    manifest = create_daily_manifest(db, "20260807")

    assert manifest["status"] == "PARTIAL"
    assert "MISSING_SCAN_RUN" in manifest["blockers"]


def test_manifest_api_is_read_only_and_returns_latest(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "api.db"
    _setup(db)
    manifest = create_daily_manifest(db, "20260807")
    monkeypatch.setattr(backend, "_DB", db)
    monkeypatch.setattr(legacy_misc, "_DB", db)
    client = TestClient(backend.app)

    listing = client.get("/api/manifests")
    detail = client.get("/api/manifests/20260807")

    assert listing.status_code == 200
    assert listing.json()["items"][0]["manifest_id"] == manifest["manifest_id"]
    assert detail.status_code == 200
    assert detail.json()["manifest_sha256"] == manifest["manifest_sha256"]


def test_v2_migrations_do_not_skip_missing_versions_when_independent_v101_exists(
    tmp_path: Path,
) -> None:
    db = tmp_path / "version-collision.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE schema_version(version INTEGER PRIMARY KEY,name TEXT NOT NULL,"
            "checksum TEXT NOT NULL,applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_version VALUES "
            "(101,'M101_logic_tables','x','2026-08-11T00:00:00+08:00')"
        )

    run_v2_migrations(db)

    with sqlite3.connect(db) as conn:
        versions = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {9, 11, 12, 13, 101}.issubset(versions)
    assert "daily_run_manifests" in tables
    assert "research_runs" in tables
