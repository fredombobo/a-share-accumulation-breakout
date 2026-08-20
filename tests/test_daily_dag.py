"""P6.1 每日 DAG 测试：幂等、attempt 保留、崩溃续跑、依赖阻断。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from ab_screener.operations.dag import DailyDag, StepSpec, idempotency_key
from ab_screener.operations.scheduler import SchedulerRunner


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "dag.db"))
    apply_pending(c)
    yield c
    c.close()


def _dag(run_log: list[str], fail_step: str | None = None) -> DailyDag:
    def make(name: str):
        def fn(**kwargs):
            if fail_step == name:
                raise RuntimeError(f"{name} 失败")
            run_log.append(name)
        return fn

    return DailyDag([
        StepSpec("a", "GLOBAL", "all", make("a")),
        StepSpec("b", "GLOBAL", "all", make("b"), depends_on=("a",)),
        StepSpec("c", "GLOBAL", "all", make("c"), depends_on=("b",)),
    ])


def test_same_key_succeeds_once(conn, tmp_path):
    db = str(tmp_path / "dag.db")
    log: list[str] = []
    runner = SchedulerRunner(db, _dag(log))
    first = runner.run_day("20260810")
    second = runner.run_day("20260810")
    assert first["status"] == "COMPLETED"
    assert second["results"]["a"]["idempotent"] is True
    assert log.count("a") == 1 and log.count("b") == 1  # 幂等不重跑


def test_attempt_retention_and_resume(conn, tmp_path):
    """首轮 b 失败 → 保留 ATTEMPT_FAILED；重跑续跑。"""
    db = str(tmp_path / "dag.db")
    log: list[str] = []
    runner = SchedulerRunner(db, _dag(log, fail_step="b"))
    first = runner.run_day("20260810")
    assert first["results"]["b"]["status"] == "FAIL"
    with sqlite3.connect(db) as c:
        statuses = [r[0] for r in c.execute(
            "SELECT status FROM dag_step_runs WHERE step_name='b' ORDER BY attempt"
        ).fetchall()]
    assert "ATTEMPT_FAILED" in statuses or "FAIL" in statuses
    # 修复后重跑：c 可完成（b 重跑成功）
    log2: list[str] = []
    runner2 = SchedulerRunner(db, _dag(log2))
    second = runner2.run_day("20260810")
    assert (second["results"]["b"].get("idempotent") is True
            or second["results"]["b"]["status"] == "SUCCESS")
    assert second["status"] == "COMPLETED"


def test_upstream_failure_blocks_dependents(conn, tmp_path):
    db = str(tmp_path / "dag.db")
    log: list[str] = []
    runner = SchedulerRunner(db, _dag(log, fail_step="a"))
    result = runner.run_day("20260810")
    assert result["results"]["a"]["status"] == "FAIL"
    assert result["results"]["b"]["status"] == "SKIPPED"
    assert result["results"]["c"]["status"] == "SKIPPED"
    assert result["status"] == "FAILED"


def test_lease_exclusive(conn, tmp_path):
    from ab_screener.data.scheduler_repository import acquire_lease

    db = str(tmp_path / "dag.db")
    with sqlite3.connect(db) as c:
        assert acquire_lease(c, lease_id="L1", holder="h1", trade_date="20260810") is True
        # 他人持有且未过期 → 拒绝
        assert acquire_lease(c, lease_id="L1", holder="h2", trade_date="20260810",
                             ttl_seconds=300) is False
        # 同 holder 幂等
        assert acquire_lease(c, lease_id="L1", holder="h1", trade_date="20260810") is True


def test_idempotency_key_structure(conn):
    key = idempotency_key("20260810", "sync", "GLOBAL", "all", "h1")
    assert key == idempotency_key("20260810", "sync", "GLOBAL", "all", "h1")
    assert key != idempotency_key("20260810", "sync", "GLOBAL", "all", "h2")
    with pytest.raises(Exception, match="scope_type"):
        idempotency_key("20260810", "sync", "BAD", "all", "h1")
