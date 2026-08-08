"""Public API acceptance checks for feature gates and idempotent writes."""
from __future__ import annotations

import importlib
import os
from pathlib import Path

from fastapi.testclient import TestClient

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
    backend._DB = db
    backend._store = LocalStore(db_path=db)
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
    backend._DB = db
    backend._store = LocalStore(db_path=db)
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


def test_scheduler_cycle_query_uses_real_schema_column() -> None:
    source = Path("web/backend_app.py").read_text(encoding="utf-8")
    assert "SELECT run_date FROM pt_cycle" in source
    assert "SELECT trade_date FROM pt_cycle" not in source
