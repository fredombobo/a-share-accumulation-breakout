"""G acceptance: identity priority, server evidence and feature enforcement."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient

from ab_screener.api.app_factory import include_v2_routers
from ab_screener.application import readiness_service
from ab_screener.application.platform_config import load_resolved_config
from ab_screener.domain.readiness import GATES, ReadinessInput, evaluate_readiness
from ab_screener.local_store import LocalStore


def _gate_results(*, research: bool = True) -> dict[str, bool]:
    return {gate: research if gate == "R" else True for gate in GATES}


def _snapshot(status: str = "BLOCKED") -> dict[str, Any]:
    return {
        "status": status,
        "reasons": ["fixture"],
        "per_gate": {gate: False for gate in GATES},
        "blocked_gates": list(GATES),
        "identity_blockers": [],
        "gates": {
            gate: {
                "gate": gate,
                "status": "INSUFFICIENT",
                "passed": False,
                "source": "fixture",
                "reason": "fixture",
            }
            for gate in GATES
        },
        "identity": {},
        "live_trading_enabled": False,
    }


def test_identity_blockers_outrank_research_only_state() -> None:
    dirty = evaluate_readiness(
        ReadinessInput(
            gate_results=_gate_results(research=False),
            worktree_clean=False,
            identity_matches=True,
        )
    )
    mismatch = evaluate_readiness(
        ReadinessInput(
            gate_results=_gate_results(research=False),
            worktree_clean=True,
            identity_matches=False,
        )
    )
    assert dirty["status"] == mismatch["status"] == "BLOCKED"
    assert dirty["identity_blockers"] == ["WORKTREE_DIRTY"]
    assert mismatch["identity_blockers"] == ["IDENTITY_MISMATCH"]


def test_only_research_fail_and_all_pass_have_exact_verdicts() -> None:
    research_blocked = evaluate_readiness(
        ReadinessInput(gate_results=_gate_results(research=False))
    )
    ready = evaluate_readiness(ReadinessInput(gate_results=_gate_results()))
    assert research_blocked["status"] == "ENGINEERING_READY_RESEARCH_BLOCKED"
    assert ready["status"] == "PERSONAL_INSTITUTIONAL_READY"


def test_signed_gate_artifact_is_bound_to_current_identity(tmp_path: Path) -> None:
    identity = {
        "git_sha": "abc",
        "code_version": "build",
        "platform_config_hash": "cfg",
        "db_fingerprint": "db",
    }
    report = {
        "gate": "S",
        "status": "PASS",
        "summary": "semantic checks passed",
        "generated_at": "2026-08-27T08:00:00+08:00",
        "identity": dict(identity),
    }
    report["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    (tmp_path / "S.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )

    passed = readiness_service._artifact_gate("S", tmp_path, identity)
    assert passed["status"] == "PASS"
    report["identity"]["git_sha"] = "other"
    unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
    report["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    (tmp_path / "S.json").write_text(json.dumps(report), encoding="utf-8")
    mismatched = readiness_service._artifact_gate("S", tmp_path, identity)
    assert mismatched["status"] == "FAIL"
    assert mismatched["identity_matches"] is False
    assert "git_sha" in mismatched["identity_mismatches"]


def test_readiness_collector_uses_authoritative_server_run(
    tmp_path: Path, monkeypatch,
) -> None:
    db = tmp_path / "readiness.db"
    LocalStore(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO research_runs (research_run_id,strategy,research_mode,can_claim_edge,"
            "config_hash,created_at,status,verdict,candidate_eligible,report_sha256,"
            "code_version,updated_at) VALUES "
            "('authoritative','s','full',0,'r-cfg','2026-08-27T00:00:00+08:00',"
            "'done','FAIL',0,'report-hash','build','2026-08-27T00:00:00+08:00')"
        )
    identity = {
        "git_sha": "git",
        "worktree_clean": True,
        "worktree_fingerprint": "fp",
        "code_version": "build",
        "config_hash": "data-cfg",
        "db_fingerprint": "db",
    }
    monkeypatch.setattr(readiness_service, "current_release_identity", lambda *_: dict(identity))
    monkeypatch.setattr(
        readiness_service,
        "load_latest_gate_report",
        lambda *_: {
            "passed": True,
            "generated_at": "2026-08-27T08:00:00+08:00",
            "code_version": "build",
            "config_hash": "data-cfg",
            "db_fingerprint": "db",
            "report_sha256": "data-hash",
        },
    )
    monkeypatch.setattr(
        readiness_service,
        "_operations_gate",
        lambda *_: {
            "gate": "O", "status": "PASS", "passed": True,
            "source": "fixture", "reason": "pass", "identity_matches": True,
        },
    )
    monkeypatch.setattr(
        readiness_service,
        "_artifact_gate",
        lambda gate, *_: {
            "gate": gate, "status": "PASS", "passed": True,
            "source": "fixture", "reason": "pass", "identity_matches": True,
        },
    )
    config = load_resolved_config(
        env={"V2_AUTHORITATIVE_RESEARCH_RUN_ID": "authoritative"}
    )
    result = readiness_service.build_readiness_snapshot(tmp_path, db, config)
    assert result["gates"]["R"]["research_run_id"] == "authoritative"
    assert result["gates"]["R"]["verdict"] == "FAIL"
    assert result["status"] == "ENGINEERING_READY_RESEARCH_BLOCKED"


def test_platform_status_always_readable_and_flags_block_server_route(
    tmp_path: Path, monkeypatch,
) -> None:
    from ab_screener.api.routers import readiness as readiness_router

    db = tmp_path / "app.db"
    LocalStore(db)
    monkeypatch.setenv("AB_DB_PATH", str(db))
    monkeypatch.setattr(readiness_router, "build_readiness_snapshot", lambda *_: _snapshot())
    app = FastAPI()
    include_v2_routers(app, include_scan_router=False)
    disabled = load_resolved_config(
        env={
            "INSTITUTIONAL_CONSOLE_V2_ENABLED": "false",
            "V2_STRATEGY_REGISTRY_ENABLED": "false",
        }
    )
    app.state.platform_config = disabled
    client = TestClient(app)

    platform = client.get("/api/v2/platform/status?D=true&R=true")
    readiness = client.get("/api/v2/readiness?D=true&R=true")
    blocked = client.get("/api/v2/desk")
    health = client.get("/api/v2/system/health")

    assert platform.status_code == readiness.status_code == 200
    assert platform.json()["product"] == "accumulation_breakout"
    assert platform.json()["live_trading_enabled"] is False
    assert readiness.json()["status"] == "BLOCKED"
    assert blocked.status_code == 503
    assert blocked.json()["detail"]["code"] == "FEATURE_DISABLED"
    assert health.status_code == 200
    assert health.headers["X-AB-Product"] == "accumulation_breakout"
    assert health.json()["build_version"] == app.state.build_version
    assert health.json()["config_hash"] == disabled["resolved_hash"]
