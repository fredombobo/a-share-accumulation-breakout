from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ab_screener.application.release_evidence import evaluate_release_readiness
from web import backend_app as backend

_TZ = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=_TZ)


def _identity(*, clean: bool = True) -> dict:
    return {
        "git_sha": "a" * 40,
        "worktree_clean": clean,
        "worktree_fingerprint": "clean" if clean else "dirty",
        "code_version": "build-1",
        "config_hash": "config-1",
        "db_fingerprint": "db-1",
    }


def _gate(*, age_hours: int = 1, passed: bool = True) -> dict:
    return {
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "generated_at": (_NOW - timedelta(hours=age_hours)).isoformat(),
        "code_version": "build-1",
        "config_hash": "config-1",
        "db_fingerprint": "db-1",
        "report_sha256": "f" * 64,
    }


def test_current_matching_gate_and_clean_tree_are_release_ready() -> None:
    result = evaluate_release_readiness(_identity(), _gate(), now=_NOW)

    assert result["ready"] is True
    assert result["status"] == "PASS"
    assert result["blockers"] == []


def test_stale_gate_dirty_tree_and_identity_mismatch_are_blocking() -> None:
    gate = _gate(age_hours=25)
    gate.update({"code_version": "old", "config_hash": "old", "db_fingerprint": "old"})

    result = evaluate_release_readiness(_identity(clean=False), gate, now=_NOW)

    assert result["ready"] is False
    codes = {item["code"] for item in result["blockers"]}
    assert codes == {
        "WORKTREE_DIRTY",
        "GATE_EXPIRED",
        "CODE_VERSION_MISMATCH",
        "CONFIG_HASH_MISMATCH",
        "DATABASE_FINGERPRINT_MISMATCH",
    }


def test_not_run_or_future_dated_gate_never_passes() -> None:
    not_run = evaluate_release_readiness(_identity(), None, now=_NOW)
    future_gate = _gate()
    future_gate["generated_at"] = (_NOW + timedelta(minutes=10)).isoformat()
    future = evaluate_release_readiness(_identity(), future_gate, now=_NOW)

    assert not_run["ready"] is False
    assert not_run["blockers"][0]["code"] == "REAL_DATA_GATE_NOT_RUN"
    assert future["ready"] is False
    assert {item["code"] for item in future["blockers"]} == {"GATE_TIME_INVALID"}


def test_release_readiness_api_is_separate_from_runtime_freshness(monkeypatch, tmp_path: Path) -> None:
    expected = {
        "status": "FAIL",
        "ready": False,
        "blockers": [{"code": "WORKTREE_DIRTY", "message": "工作区存在未提交变更"}],
        "identity": _identity(clean=False),
        "gate_report_sha256": "f" * 64,
    }
    monkeypatch.setattr(
        "ab_screener.application.release_evidence.build_release_evidence",
        lambda project_root, db_path: expected,
    )
    monkeypatch.setattr(backend, "_DB", tmp_path / "release.db")
    client = TestClient(backend.app)

    response = client.get("/api/release/readiness")

    assert response.status_code == 200
    assert response.json() == expected
