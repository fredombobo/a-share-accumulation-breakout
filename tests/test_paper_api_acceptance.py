"""Public API acceptance checks for feature gates and idempotent writes."""
from __future__ import annotations

import importlib
import os
import sqlite3
from pathlib import Path

from starlette.testclient import TestClient

from ab_screener.api.routers import legacy_paper
from local_store import LocalStore


def _load_backend_without_scheduler():
    previous = os.environ.get("PAPER_TRADING_ENABLED")
    os.environ["PAPER_TRADING_ENABLED"] = "false"
    try:
        module = importlib.import_module("web.backend_app")
    finally:
        if previous is None:
            os.environ.pop("PAPER_TRADING_ENABLED", None)
        else:
            os.environ["PAPER_TRADING_ENABLED"] = previous
    return module


def test_paper_write_api_requires_and_replays_idempotency_key(tmp_path: Path) -> None:
    backend = _load_backend_without_scheduler()
    db = tmp_path / "api.db"
    backend._DB = legacy_paper._DB = db
    backend._store = legacy_paper._store = LocalStore(db_path=db)
    client = TestClient(backend.app)

    missing = client.post("/api/paper/account", json={"initial_cash_fen": "10000"})
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    headers = {"Idempotency-Key": "api-acceptance-key"}
    first = client.post(
        "/api/paper/account", json={"initial_cash_fen": "10000"}, headers=headers
    )
    replay = client.post(
        "/api/paper/account", json={"initial_cash_fen": "10000"}, headers=headers
    )
    conflict = client.post(
        "/api/paper/account", json={"initial_cash_fen": "10001"}, headers=headers
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_feature_flag_and_legacy_portfolio_write_are_blocked(tmp_path: Path) -> None:
    backend = _load_backend_without_scheduler()
    db = tmp_path / "api.db"
    backend._DB = legacy_paper._DB = db
    backend._store = legacy_paper._store = LocalStore(db_path=db)
    client = TestClient(backend.app)

    legacy = client.post("/api/portfolio", json={"action": "remove", "ts_code": "000001.SZ"})
    assert legacy.status_code == 409
    assert legacy.json()["detail"]["code"] == "PORTFOLIO_READ_ONLY_MIGRATION"

    previous = os.environ.get("PAPER_TRADING_ENABLED")
    os.environ["PAPER_TRADING_ENABLED"] = "false"
    try:
        disabled = client.get("/api/paper/dashboard")
        status = client.get("/api/paper/gates/status")
    finally:
        if previous is None:
            os.environ.pop("PAPER_TRADING_ENABLED", None)
        else:
            os.environ["PAPER_TRADING_ENABLED"] = previous
    assert disabled.status_code == 503
    assert disabled.json()["detail"]["code"] == "PAPER_TRADING_DISABLED"
    assert status.status_code == 200
    assert status.json()["paper_enabled"] is False


def test_openapi_contains_corporate_action_apply_endpoint() -> None:
    backend = _load_backend_without_scheduler()
    paths = backend.app.openapi()["paths"]
    assert "/api/paper/corporate-actions/{action_id}/apply" in paths
    assert "/api/paper/trading-calendar" in paths
    assert "/api/paper/orders/review" in paths


def test_scheduler_cycle_query_uses_real_schema_column() -> None:
    source = Path("ab_screener/data/paper_query.py").read_text(encoding="utf-8")
    assert "SELECT run_date FROM pt_cycle" in source
    assert "SELECT trade_date FROM pt_cycle" not in source


def test_health_exposes_guided_ui_feature_flag() -> None:
    backend = _load_backend_without_scheduler()
    client = TestClient(backend.app)
    previous = os.environ.get("GUIDED_UI_ENABLED")
    os.environ["GUIDED_UI_ENABLED"] = "false"
    try:
        response = client.get("/api/health")
    finally:
        if previous is None:
            os.environ.pop("GUIDED_UI_ENABLED", None)
        else:
            os.environ["GUIDED_UI_ENABLED"] = previous
    assert response.status_code == 200
    assert response.json()["guided_ui_enabled"] is False


def test_historical_manual_draft_api_uses_selected_execution_date(tmp_path: Path) -> None:
    backend = _load_backend_without_scheduler()
    db = tmp_path / "historical-api.db"
    backend._DB = legacy_paper._DB = db
    backend._store = legacy_paper._store = LocalStore(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO daily (ts_code, trade_date, open, high, low, close, vol, amount)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ("000001.SZ", "20260805", 10, 10.2, 9.8, 10.1, 10000, 100000),
                ("000001.SZ", "20260806", 10.2, 10.5, 10.1, 10.4, 10000, 100000),
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (cal_date,is_open,source,updated_at)"
            " VALUES (?,?,'local_infer','t')",
            [("20260805", 1), ("20260806", 1)],
        )

    client = TestClient(backend.app)
    account = client.post(
        "/api/paper/account",
        json={"initial_cash_fen": "50000000"},
        headers={"Idempotency-Key": "historical-account"},
    )
    assert account.status_code == 200
    draft = client.post(
        "/api/paper/orders/drafts",
        json={
            "side": "BUY",
            "mode": "MANUAL_HISTORY",
            "ts_code": "000001",
            "execution_trade_date": "20260806",
            "qty": 100,
        },
        headers={"Idempotency-Key": "historical-draft"},
    )
    assert draft.status_code == 200, draft.text
    payload = draft.json()
    assert payload["source"] == "MANUAL_HISTORY"
    assert payload["ts_code"] == "000001.SZ"
    assert payload["eligible_trade_date"] == "20260806"


def test_dashboard_hides_snapshots_invalidated_by_historical_replay(tmp_path: Path) -> None:
    backend = _load_backend_without_scheduler()
    db = tmp_path / "dashboard-replay.db"
    backend._DB = legacy_paper._DB = db
    backend._store = legacy_paper._store = LocalStore(db_path=db)
    client = TestClient(backend.app)
    created = client.post(
        "/api/paper/account",
        json={"initial_cash_fen": "50000000"},
        headers={"Idempotency-Key": "dashboard-account"},
    )
    assert created.status_code == 200
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO pt_cycle (cycle_id,run_date,phase,started_at,finished_at)"
            " VALUES (?,?,?,'2026-08-08T00:00:00+08:00','2026-08-08T00:01:00+08:00')",
            [
                ("CY-20260805", "20260805", "DONE"),
                ("CY-20260806", "20260806", "PRE_OPEN"),
            ],
        )
        conn.executemany(
            "INSERT INTO pt_daily_snapshot (account_id,trade_date,cash_fen,market_value_fen,"
            " total_asset_fen,positions_json) VALUES (1,?,?,?,?, '[]')",
            [
                ("20260805", 50_000_000, 0, 50_000_000),
                ("20260806", 49_000_000, 1_000_000, 50_000_000),
            ],
        )
    dashboard = client.get("/api/paper/dashboard")
    assert dashboard.status_code == 200
    assert [row["trade_date"] for row in dashboard.json()["equity_curve"]] == ["20260805"]


def test_tutorial_review_api_needs_no_idempotency_and_changes_no_ledger(tmp_path: Path) -> None:
    backend = _load_backend_without_scheduler()
    db = tmp_path / "tutorial-review-api.db"
    backend._DB = legacy_paper._DB = db
    backend._store = legacy_paper._store = LocalStore(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO daily (ts_code,trade_date,open,high,low,close,vol,amount)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ("000001.SZ", "20260805", 10.0, 10.2, 9.9, 10.1, 10_000, 101_000),
                ("000001.SZ", "20260806", 10.2, 10.5, 10.1, 10.4, 10_000, 104_000),
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (cal_date,is_open,source,updated_at)"
            " VALUES (?,?, 'tushare','t')",
            [("20260805", 1), ("20260806", 1)],
        )
        before = conn.execute("SELECT COUNT(*) FROM pt_audit_event").fetchone()[0]

    client = TestClient(backend.app)
    response = client.post(
        "/api/paper/orders/review",
        json={
            "scope": "TUTORIAL", "side": "BUY", "mode": "MANUAL_HISTORY",
            "ts_code": "000001", "execution_trade_date": "20260806", "qty": 100,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["persisted"] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pt_audit_event").fetchone()[0] == before
