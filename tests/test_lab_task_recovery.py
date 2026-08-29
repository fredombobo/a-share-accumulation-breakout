from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import ab_screener.api.routers.legacy_lab as backend
from ab_screener.research.store import ResearchRunStore
from local_store import LocalStore


def test_status_without_id_prefers_persisted_active_task_over_terminal_task(
    monkeypatch, tmp_path,
) -> None:
    store = ResearchRunStore(tmp_path / "status.db")
    store.create_run(
        "done-new", strategy="A", research_mode="full", request={}, input_hash="done",
        dataset_version="d", code_version="c", cost_version="k",
    )
    store.update("done-new", status="done", progress=100)
    store.create_run(
        "active-old", strategy="A", research_mode="full", request={}, input_hash="active",
        dataset_version="d", code_version="c", cost_version="k",
    )
    store.update("active-old", status="running", progress=42, message="OOS")
    monkeypatch.setattr(backend, "_LAB_STORE", store)

    result = backend.lab_status()

    assert result["task_id"] == "active-old"
    assert result["status"] == "running"


def test_strategy_lab_is_offline_only_and_not_shipped_in_frontend() -> None:
    """研究运行可恢复，但失败研究不再作为 8001 日用产品页面发布。"""
    frontend_root = Path("web/frontend/src")
    assert not (frontend_root / "pages/StrategyLab.tsx").exists()

    app_source = (frontend_root / "App.tsx").read_text(encoding="utf-8")
    sidebar_source = (frontend_root / "layout/Sidebar.tsx").read_text(
        encoding="utf-8"
    )
    assert 'path="/lab"' not in app_source
    assert "策略实验室" not in sidebar_source


def test_status_and_report_can_be_restored_from_sqlite(monkeypatch, tmp_path) -> None:
    db = tmp_path / "lab.db"
    LocalStore(db)
    store = ResearchRunStore(db)
    store.create_run(
        "persisted", strategy="A", research_mode="full",
        request={"_windows": {"is_start": "2023", "oos_end": "2026"}},
        input_hash="h", dataset_version="d", code_version="c", cost_version="k",
    )
    store.update(
        "persisted", status="done", progress=100, verdict="FAIL",
        result={"trusted_report": {"verdict": "FAIL", "summary": "回撤未通过"}},
        report_markdown="# FAIL",
    )
    monkeypatch.setattr(backend, "_LAB_STORE", store)
    original = dict(backend._LAB_TASKS)
    backend._LAB_TASKS.clear()
    try:
        restored = backend.lab_status("persisted")
        latest = backend.lab_latest_report()
    finally:
        backend._LAB_TASKS.update(original)

    assert restored["status"] == "done"
    assert restored["result"]["trusted_report"]["verdict"] == "FAIL"
    assert latest["report"]["summary"] == "回撤未通过"


def test_worker_persists_pass_only_as_isolated_candidate(monkeypatch, tmp_path) -> None:
    db = tmp_path / "worker.db"
    LocalStore(db)
    store = ResearchRunStore(db)
    store.create_run(
        "worker-run", strategy="A", research_mode="full",
        request={"strategy": "A", "mode": "grid", "_windows": {}},
        input_hash="h", dataset_version="d", code_version="c", cost_version="k",
    )
    monkeypatch.setattr(backend, "_LAB_STORE", store)

    captured: dict = {}

    def fake_execute(**_kwargs):
        captured.update(_kwargs)
        report = {
            "research_run_id": "worker-run", "verdict": "PASS",
            "candidate_eligible": True, "summary": "通过", "markdown": "# PASS",
            "anti_overfit": {"verdict": "PASS", "version": "personal-anti-overfit-v1"},
        }
        return {
            "is_top": [{"param_id": "p1", "strategy": "A"}],
            "is_all": [{"param_id": "p1", "strategy": "A"}],
            "oos": [{"param_id": "p1", "oos_net_profit_factor": 1.2}],
            "baselines": {}, "promotion_checks": {"verdict": "PASS"},
            "trusted_report": report,
            "frozen_candidate": {
                "is": {"param_id": "p1", "strategy": "A", "exit_window": 10},
                "oos": {"oos_net_profit_factor": 1.2},
            },
            "checkpoint": {"gate": {"verdict": "PASS"}},
        }

    monkeypatch.setattr("ab_screener.research.trusted_run.execute_trusted_research", fake_execute)
    original = dict(backend._LAB_TASKS)
    backend._LAB_TASKS.clear()
    backend._LAB_TASKS["worker-run"] = {
        "status": "pending", "progress": 0, "strategy": "A", "windows": {},
    }
    try:
        backend._run_lab_worker("worker-run", backend.LabOptimizeRequest(), {})
        persisted = store.get("worker-run")
    finally:
        backend._LAB_TASKS.clear()
        backend._LAB_TASKS.update(original)

    with sqlite3.connect(db) as conn:
        isolated, metrics_json = conn.execute(
            "SELECT status,metrics_json FROM research_candidates"
        ).fetchone()
        active = conn.execute("SELECT COUNT(*) FROM strategy_params WHERE status='active'").fetchone()[0]
    assert persisted is not None and persisted["status"] == "done"
    assert callable(captured["cancel_check"])
    assert isolated == "isolated"
    assert json.loads(metrics_json)["anti_overfit_version"] == "personal-anti-overfit-v1"
    assert active == 0


def test_persisted_state_wins_when_runtime_cache_disagrees(monkeypatch, tmp_path) -> None:
    db = tmp_path / "authoritative.db"
    store = ResearchRunStore(db)
    store.create_run(
        "authoritative-run",
        strategy="A",
        research_mode="full",
        request={"mode": "grid"},
        input_hash="authoritative-hash",
        dataset_version="data-v1",
        code_version="code-v1",
        cost_version="cost-v1",
    )
    store.update("authoritative-run", status="interrupted", message="service restarted")

    monkeypatch.setattr(backend, "_LAB_STORE", store)
    original = dict(backend._LAB_TASKS)
    backend._LAB_TASKS.clear()
    backend._LAB_TASKS["authoritative-run"] = {
        "status": "running",
        "phase": "OOS",
        "progress": 70,
        "message": "stale memory state",
    }
    try:
        restored = backend.lab_status("authoritative-run")
        assert restored["status"] == "interrupted"
        assert restored["message"] == "service restarted"
    finally:
        backend._LAB_TASKS.clear()
        backend._LAB_TASKS.update(original)


def test_worker_does_not_claim_user_cancel_without_persisted_cancel_request(
    monkeypatch, tmp_path,
) -> None:
    from optimizer import ResearchCancelled

    db = tmp_path / "unexpected-cancel.db"
    store = ResearchRunStore(db)
    store.create_run(
        "unexpected-cancel",
        strategy="A",
        research_mode="full",
        request={"mode": "grid"},
        input_hash="unexpected",
        dataset_version="data",
        code_version="code",
        cost_version="cost",
    )
    monkeypatch.setattr(backend, "_LAB_STORE", store)

    def fake_execute(**_kwargs):
        raise ResearchCancelled("unexpected cancellation path")

    monkeypatch.setattr("ab_screener.research.trusted_run.execute_trusted_research", fake_execute)
    original = dict(backend._LAB_TASKS)
    backend._LAB_TASKS.clear()
    backend._LAB_TASKS["unexpected-cancel"] = {"status": "pending", "progress": 0}
    try:
        backend._run_lab_worker(
            "unexpected-cancel", backend.LabOptimizeRequest(), {"mode": "full"}
        )
        persisted = store.get("unexpected-cancel")
    finally:
        backend._LAB_TASKS.clear()
        backend._LAB_TASKS.update(original)

    assert persisted is not None
    assert persisted["status"] == "error"
    assert "未收到取消请求" in persisted["message"]


def test_process_pool_child_does_not_mark_parent_lab_run_interrupted(monkeypatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        backend._LAB_STORE,
        "mark_orphaned_interrupted",
        lambda: called.append(True) or 1,
    )

    result = backend._recover_orphaned_lab_runs("SpawnProcess-1")

    assert result == 0
    assert called == []
