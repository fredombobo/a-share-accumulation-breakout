from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ab_screener.api.deps import get_db_path
from ab_screener.application.today_guide import build_today_guide
from ab_screener.data.migrations_v2 import run_v2_migrations
from local_store import LocalStore
from paper_trading.account import create_account
from paper_trading.migrations import run_migrations
from web import backend_app as backend

_TZ = ZoneInfo("Asia/Shanghai")


def _setup(db: Path) -> None:
    LocalStore(db_path=db)
    run_migrations(db)
    run_v2_migrations(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO daily(ts_code,trade_date,open,high,low,close,vol,amount) "
            "VALUES ('000001.SZ','20260807',10,10,10,10,1000,10000)"
        )
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal(cal_date,is_open,source,updated_at) "
            "VALUES (?,?,'tushare','t')",
            [("20260807", 1), ("20260808", 0), ("20260809", 0), ("20260810", 1)],
        )


def _add_successful_scan(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO scan_runs(run_id,task_id,as_of,strategy_snapshot_json,config_hash,"
            "git_sha,dataset_version,input_hash,result_hash,research_mode,status,created_at) "
            "VALUES ('scan-1','task-1','20260807','{}','cfg','code','data','input','result',"
            "'full','SUCCEEDED','2026-08-07T16:00:00+08:00')"
        )


def test_stale_market_data_is_the_only_next_action(tmp_path: Path) -> None:
    db = tmp_path / "stale.db"
    _setup(db)

    guide = build_today_guide(db, now=datetime(2026, 8, 10, 18, 0, tzinfo=_TZ))

    assert guide["next_action"] == "SYNC_DATA"
    assert guide["primary_label"] == "同步最新行情"
    assert guide["expected_market_date"] == "20260810"


def test_active_scan_wins_over_starting_another_scan(tmp_path: Path) -> None:
    db = tmp_path / "active-scan.db"
    _setup(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO scan_jobs(task_id,status,top_n,days,cancel_requested,created_at,updated_at) "
            "VALUES ('task-running','RUNNING',20,160,0,'t','t')"
        )

    guide = build_today_guide(db, now=datetime(2026, 8, 7, 18, 0, tzinfo=_TZ))

    assert guide["next_action"] == "WAIT_SCAN"
    assert guide["task_id"] == "task-running"


def test_current_data_without_scan_recommends_scan(tmp_path: Path) -> None:
    db = tmp_path / "scan.db"
    _setup(db)

    guide = build_today_guide(db, now=datetime(2026, 8, 7, 18, 0, tzinfo=_TZ))

    assert guide["next_action"] == "RUN_SCAN"


def test_scan_complete_then_recommends_account_or_settlement(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _setup(db)
    _add_successful_scan(db)

    without_account = build_today_guide(db, now=datetime(2026, 8, 7, 18, 0, tzinfo=_TZ))
    assert without_account["next_action"] == "CREATE_ACCOUNT"

    create_account(db, 1_000_000)
    with_account = build_today_guide(db, now=datetime(2026, 8, 7, 18, 0, tzinfo=_TZ))
    assert with_account["next_action"] == "RUN_SETTLEMENT"


def test_open_reconciliation_blocks_other_daily_actions(tmp_path: Path) -> None:
    db = tmp_path / "reconciliation.db"
    _setup(db)
    _add_successful_scan(db)
    create_account(db, 1_000_000)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO pt_reconciliation(run_date,result,diff_json,severity,status,checked_at) "
            "VALUES ('20260807','DIFF','[]','CRITICAL','OPEN','t')"
        )

    guide = build_today_guide(db, now=datetime(2026, 8, 7, 18, 0, tzinfo=_TZ))

    assert guide["next_action"] == "RESOLVE_RECONCILIATION"


def test_today_api_returns_the_server_derived_action(tmp_path: Path) -> None:
    db = tmp_path / "today-api.db"
    _setup(db)
    backend.app.dependency_overrides[get_db_path] = lambda: str(db)
    try:
        client = TestClient(backend.app)
        response = client.get("/api/today", params={"at": "2026-08-07T18:00:00+08:00"})
    finally:
        backend.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["next_action"] == "RUN_SCAN"
