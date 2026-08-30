from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI
from starlette.testclient import TestClient

from ab_screener.api.deps import get_db_path
from ab_screener.api.routers.professional_backtest import router
from ab_screener.data.strategy_profile_repository import StrategyProfileRepository
from ab_screener.domain.profile import StrategyProfile, default_profile
from ab_screener.research.store import ResearchRunStore
from ab_screener.screener import evaluator
from local_store import LocalStore


def _client(path: Path) -> TestClient:
    LocalStore(db_path=path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_path] = lambda: str(path)
    return TestClient(app)


def _result(verdict: str = "EXPLORATORY_PROMISING") -> dict[str, Any]:
    selected = {
        "param_id": "parameter-01",
        "signal": {
            "box_min_days": 60,
            "box_max_days": 200,
            "box_max_amp": 0.24,
            "breakout_vol_ratio": 1.8,
            "breakout_chg_min": 0.02,
            "breakout_chg_max": 0.095,
            "breakout_vs_recent_vol_ratio": 1.3,
            "breakout_window_days": 5,
            "require_structure": True,
        },
        "exit": {
            "vol_ratio_min": 1.5,
            "strong_reset": 3,
            "exit_window": 10,
            "stop_pct": 0.05,
        },
        "is": {"net_n_trades": 50, "portfolio_total_return": 0.12},
        "oos": {"net_n_trades": 42, "portfolio_total_return": 0.08},
    }
    return {
        "verdict": verdict,
        "verdict_label": "探索结果值得另行预登记复验",
        "selected": selected,
        "wf": {"wf_pass": True, "evidence_complete": True},
        "cost_stress": {"metrics": {"portfolio_total_return": 0.04}},
        "baselines": {
            "random": {"portfolio_total_return": 0.01},
            "ma20_60": {"portfolio_total_return": -0.02},
        },
    }


def _completed_task(
    path: Path,
    task_id: str,
    *,
    verdict: str = "EXPLORATORY_PROMISING",
    code_version: str = "code-v1",
    dataset_version: str = "cutoff-v1",
) -> dict[str, Any]:
    store = ResearchRunStore(path)
    store.create_run(
        task_id,
        strategy="A",
        research_mode="professional_grid",
        request={"contract_version": "test"},
        input_hash=f"input-{task_id}",
        dataset_version=dataset_version,
        code_version=code_version,
        cost_version="cost-v1",
        config_hash="grid-v1",
    )
    return store.update(
        task_id,
        status="done",
        phase="DONE",
        progress=100,
        result=_result(verdict),
        verdict=verdict,
        candidate_eligible=False,
        can_claim_edge=False,
    )


def test_profile_activation_is_manual_idempotent_and_reversible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "closed-loop.db"
    client = _client(path)
    _completed_task(path, "probt-pass")
    monkeypatch.setattr(
        "ab_screener.application.strategy_profile_service.build_version",
        lambda: "code-v1",
    )
    monkeypatch.setattr(
        "ab_screener.application.strategy_profile_service.latest_research_cutoff",
        lambda _: "cutoff-v1",
    )

    initial = client.get("/api/backtest/profile")
    status = client.get("/api/backtest/status/probt-pass")
    unacknowledged = client.post(
        "/api/backtest/profile/activate",
        json={"task_id": "probt-pass", "acknowledge_exploratory": False},
    )
    activated = client.post(
        "/api/backtest/profile/activate",
        json={"task_id": "probt-pass", "acknowledge_exploratory": True},
    )
    repeated = client.post(
        "/api/backtest/profile/activate",
        json={"task_id": "probt-pass", "acknowledge_exploratory": True},
    )

    assert initial.status_code == status.status_code == activated.status_code == 200
    assert unacknowledged.status_code == 409
    assert unacknowledged.json()["detail"]["code"] == "PROFILE_ACKNOWLEDGEMENT_REQUIRED"
    assert initial.json()["active"]["is_default"] is True
    assert status.json()["profile_activation"]["can_activate"] is True
    active = activated.json()["active"]
    assert active["is_default"] is False
    assert active["source"]["task_id"] == "probt-pass"
    assert active["entry"]["box_max_days"] == 200
    assert active["entry"]["breakout_vol_ratio"] == 1.8
    assert active["exit_reference"]["stop_pct"] == 0.05
    assert active["required_scan_days"] == 210
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True
    assert repeated.json()["active"]["config_hash"] == active["config_hash"]

    reset = client.post("/api/backtest/profile/reset", json={"confirm": True})
    assert reset.status_code == 200
    assert reset.json()["active"]["is_default"] is True
    assert reset.json()["history"][0]["status"] == "retired"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM strategy_profiles").fetchone()[0] == 1


def test_weak_or_stale_backtest_is_fail_closed(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "blocked.db"
    client = _client(path)
    _completed_task(path, "probt-weak", verdict="EXPLORATORY_WEAK")
    _completed_task(path, "probt-stale", code_version="old-code", dataset_version="old-data")
    monkeypatch.setattr(
        "ab_screener.application.strategy_profile_service.build_version",
        lambda: "code-v1",
    )
    monkeypatch.setattr(
        "ab_screener.application.strategy_profile_service.latest_research_cutoff",
        lambda _: "cutoff-v1",
    )

    weak = client.post(
        "/api/backtest/profile/activate",
        json={"task_id": "probt-weak", "acknowledge_exploratory": True},
    )
    stale = client.post(
        "/api/backtest/profile/activate",
        json={"task_id": "probt-stale", "acknowledge_exploratory": True},
    )

    assert weak.status_code == stale.status_code == 409
    weak_checks = weak.json()["detail"]["details"]["checks"]
    stale_checks = stale.json()["detail"]["details"]["checks"]
    assert any(item["code"] == "EVIDENCE_PROMISING" and not item["passed"] for item in weak_checks)
    assert any(item["code"] == "CODE_IDENTITY_CURRENT" and not item["passed"] for item in stale_checks)
    assert any(item["code"] == "DATASET_IDENTITY_CURRENT" and not item["passed"] for item in stale_checks)
    assert StrategyProfileRepository(path).effective()["profile"].is_default


def test_profile_signal_contract_is_forwarded_unchanged_to_strict_detector(monkeypatch) -> None:
    profile = StrategyProfile(
        **{
            **default_profile().to_canonical_dict(),
            "profile_id": "test-custom",
            "version": "run-1",
            "source_kind": "PROFESSIONAL_BACKTEST",
            "box_min_days": 60,
            "box_max_days": 200,
            "breakout_vol_ratio": 1.8,
        }
    )
    captured: dict[str, Any] = {}

    def fake_detect_many(codes, daily, **kwargs):
        captured.update(kwargs)
        return {next(iter(codes)): {"is_breakout": True}}

    monkeypatch.setattr(evaluator, "detect_many", fake_detect_many)
    signals: dict[str, dict] = {}
    hits = evaluator._detect_on_codes(
        {"000001.SZ"},
        pd.DataFrame(),
        signals,
        strict_kwargs=profile.signal_kwargs(),
        workers=1,
    )

    assert hits == ["000001.SZ"]
    assert captured["kwargs"] == profile.signal_kwargs()
    assert signals["000001.SZ"]["is_breakout"] is True
