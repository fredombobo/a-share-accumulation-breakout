from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from ab_screener.api.deps import get_db_path
from ab_screener.api.routers.lean_ai_review import router as ai_router
from ab_screener.api.routers.professional_backtest import router as backtest_router
from local_store import LocalStore


def _research_db(path: Path) -> Path:
    LocalStore(db_path=path)
    codes = [f"{index:06d}.SZ" for index in range(25)]
    dates: list[str] = []
    cursor = date(2025, 1, 1)
    while len(dates) < 220:
        if cursor.weekday() < 5:
            dates.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO stock_basic(ts_code,name,industry) VALUES (?,?,?)",
            [(code, f"测试{index}", "半导体") for index, code in enumerate(codes)],
        )
        conn.executemany(
            "INSERT INTO daily(ts_code,trade_date,open,high,low,close,vol,amount,pct_chg) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(codes[0], day, 10, 10.2, 9.8, 10.1, 1000, 10100, 1.0) for day in dates],
        )
    return path


def _client(db: Path, *routers) -> TestClient:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    app.dependency_overrides[get_db_path] = lambda: str(db)
    return TestClient(app)


def test_professional_preview_returns_frozen_multi_parameter_contract(tmp_path: Path) -> None:
    db = _research_db(tmp_path / "api.db")
    client = _client(db, backtest_router)

    catalog = client.get("/api/backtest/catalog")
    universe = client.get("/api/backtest/universe")
    preview = client.post(
        "/api/backtest/preview",
        json={
            "strategy": "A",
            "sample_step": 10,
            "max_codes": 25,
            "parameters": {},
            "universe": {"industries": ["半导体"], "codes": []},
            "conditions": [],
            "windows": {"mode": "auto"},
        },
    )

    assert catalog.status_code == universe.status_code == preview.status_code == 200
    payload = preview.json()["prepared"]
    assert payload["parameter_space"]["count"] == 144
    assert payload["parameter_space"]["horizon"] >= 265
    assert payload["universe"]["count"] == 25
    assert len(payload["universe"]["sha256"]) == 64
    assert payload["research_boundary"]["candidate_eligible"] is False


def test_professional_preview_and_ai_api_fail_with_structured_reasons(tmp_path: Path) -> None:
    db = _research_db(tmp_path / "errors.db")
    client = _client(db, backtest_router, ai_router)
    oversized = client.post(
        "/api/backtest/preview",
        json={
            "parameters": {
                "box_min_days": {"mode": "range", "start": 20, "stop": 200, "step": 10},
                "box_max_days": {"mode": "range", "start": 40, "stop": 240, "step": 10},
                "breakout_vol_ratio": {"mode": "range", "start": 1, "stop": 4, "step": 0.1},
            },
        },
    )
    local_review = client.get("/api/ai-review/000000.SZ")
    invalid_provider = client.post(
        "/api/ai-review/000000.SZ/generate", json={"provider": "unknown"}
    )

    assert oversized.status_code == 422
    assert oversized.json()["detail"]["code"] == "COMBINATION_LIMIT_EXCEEDED"
    assert local_review.status_code == 200
    assert local_review.json()["boundary"]["read_only"] is True
    assert invalid_provider.status_code == 422
    assert invalid_provider.json()["detail"]["code"] == "UNKNOWN_AI_PROVIDER"
