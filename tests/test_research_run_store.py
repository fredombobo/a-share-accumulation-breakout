from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from ab_screener.data.migrations_v2 import run_v2_migrations
from ab_screener.research.store import ActiveResearchRunError, ResearchRunStore
from local_store import LocalStore


def test_v11_migration_is_repeatable_and_quarantines_legacy_active(tmp_path) -> None:
    db = tmp_path / "research.db"
    LocalStore(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO strategy_params(param_id,strategy,params_json,status) VALUES (?,?,?,?)",
            ("old-active", "A", "{}", "active"),
        )
    # Re-run explicitly to prove idempotence, including after user data exists.
    assert run_v2_migrations(db) >= 11
    assert run_v2_migrations(db) >= 11

    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
        status = conn.execute(
            "SELECT status FROM strategy_params WHERE param_id='old-active'"
        ).fetchone()[0]
        candidate_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_candidates'"
        ).fetchone()
    assert {"status", "phase", "progress", "checkpoint_json", "result_json", "verdict"} <= columns
    # Rows created after migration are not silently rewritten by a repeated migration.
    assert status == "active"
    assert candidate_table is not None


def test_v11_quarantines_active_rows_present_at_upgrade_time(tmp_path) -> None:
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE daily(ts_code TEXT, trade_date TEXT);
            CREATE TABLE strategy_params(
                param_id TEXT PRIMARY KEY, strategy TEXT, params_json TEXT, status TEXT
            );
            INSERT INTO strategy_params VALUES ('legacy', 'A', '{}', 'active');
            CREATE TABLE schema_version(
                version INTEGER PRIMARY KEY, name TEXT, checksum TEXT, applied_at TEXT
            );
            """
        )

    run_v2_migrations(db)

    with sqlite3.connect(db) as conn:
        status = conn.execute("SELECT status FROM strategy_params WHERE param_id='legacy'").fetchone()[0]
    assert status == "legacy_unverified"


def test_research_run_state_and_report_survive_new_store_instance(tmp_path) -> None:
    db = tmp_path / "persist.db"
    LocalStore(db)
    first = ResearchRunStore(db)
    first.create_run(
        "run-1",
        strategy="A",
        research_mode="full",
        request={"strategy": "A", "mode": "grid"},
        input_hash="same-input",
        dataset_version="daily:abc",
        code_version="code-1",
        cost_version="cost-1",
    )
    first.update(
        "run-1",
        status="running",
        phase="OOS",
        progress=45,
        message="样本外验证",
        checkpoint={"is_top": [{"param_id": "p1"}]},
    )

    restored = ResearchRunStore(db).latest_active()

    assert restored is not None
    assert restored["research_run_id"] == "run-1"
    assert restored["phase"] == "OOS"
    assert restored["checkpoint"]["is_top"][0]["param_id"] == "p1"

    first.update(
        "run-1",
        status="done",
        phase="CANDIDATE",
        progress=100,
        result={"trusted_report": {"verdict": "FAIL"}},
        verdict="FAIL",
        report_markdown="# FAIL",
    )
    cached = ResearchRunStore(db).completed_by_input_hash("same-input")
    assert cached is not None
    assert cached["result"]["trusted_report"]["verdict"] == "FAIL"


def test_pass_candidate_is_isolated_from_strategy_params(tmp_path) -> None:
    db = tmp_path / "candidate.db"
    LocalStore(db)
    store = ResearchRunStore(db)
    store.create_run(
        "run-pass", strategy="A", research_mode="full", request={}, input_hash="h",
        dataset_version="d", code_version="c", cost_version="k",
    )
    candidate = store.add_candidate(
        "run-pass", strategy="A", param_id="p1", params={"exit_window": 10}, metrics={"pf": 1.2}
    )

    with sqlite3.connect(db) as conn:
        strategy_count = conn.execute("SELECT COUNT(*) FROM strategy_params").fetchone()[0]
        isolated_count = conn.execute("SELECT COUNT(*) FROM research_candidates").fetchone()[0]
    assert candidate["status"] == "isolated"
    assert strategy_count == 0
    assert isolated_count == 1


def test_concurrent_create_allows_only_one_active_research_run(tmp_path) -> None:
    db = tmp_path / "single-active.db"
    store = ResearchRunStore(db)

    def create(run_id: str) -> str:
        try:
            store.create_run(
                run_id,
                strategy="A",
                research_mode="full",
                request={"mode": "grid"},
                input_hash=f"hash-{run_id}",
                dataset_version="data-v1",
                code_version="code-v1",
                cost_version="cost-v1",
            )
            return "created"
        except ActiveResearchRunError:
            return "active"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create, ("run-a", "run-b")))

    assert sorted(outcomes) == ["active", "created"]
    assert store.latest_active() is not None


def test_cancel_request_is_persisted_and_idempotent(tmp_path) -> None:
    db = tmp_path / "cancel.db"
    store = ResearchRunStore(db)
    store.create_run(
        "run-cancel",
        strategy="A",
        research_mode="full",
        request={"mode": "grid"},
        input_hash="cancel-hash",
        dataset_version="data-v1",
        code_version="code-v1",
        cost_version="cost-v1",
    )

    first = store.request_cancel("run-cancel")
    second = store.request_cancel("run-cancel")

    assert first["status"] == "cancelling"
    assert second["status"] == "cancelling"
    assert store.is_cancel_requested("run-cancel") is True


def test_concurrent_resume_starts_only_one_worker_slot(tmp_path) -> None:
    db = tmp_path / "resume.db"
    store = ResearchRunStore(db)
    store.create_run(
        "run-resume",
        strategy="A",
        research_mode="full",
        request={"mode": "grid"},
        input_hash="resume-hash",
        dataset_version="data-v1",
        code_version="code-v1",
        cost_version="cost-v1",
    )
    store.update("run-resume", status="interrupted", message="restart")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _n: store.resume_run("run-resume"), range(2)))

    assert sorted(outcomes) == [False, True]
    assert store.get("run-resume")["status"] == "pending"
