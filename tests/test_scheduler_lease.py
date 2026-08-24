"""租约生命周期（§5）：原子抢占、同 holder 续租、他人未过期拒绝、过期接管、退出释放、
并发 runner 单租约。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

import ab_screener.data.scheduler_repository as repo
from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.scheduler_repository import (
    acquire_lease,
    lease_status,
    release_lease,
    renew_lease,
)
from ab_screener.operations.dag import DailyDag, StepSpec
from ab_screener.operations.scheduler import SchedulerRunner


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    db = str(tmp_path / "lease.db")
    with sqlite3.connect(db) as c:
        apply_pending(c)
    return db


def test_acquire_renew_reject_takeover_release(db_path):
    with sqlite3.connect(db_path) as c:
        assert acquire_lease(c, lease_id="L", holder="h1", trade_date="20260810",
                             ttl_seconds=300) is True
        # 同 holder 续租成功且延长到期
        before = lease_status(c, "L")
        assert renew_lease(c, lease_id="L", holder="h1", ttl_seconds=600) is True
        after = lease_status(c, "L")
        assert after["expires_at"] > before["expires_at"]
        # 他人未过期 → 拒绝 acquire 与 renew
        assert acquire_lease(c, lease_id="L", holder="h2", trade_date="20260810",
                             ttl_seconds=300) is False
        assert renew_lease(c, lease_id="L", holder="h2") is False
        # 非持有者 release → False
        assert release_lease(c, lease_id="L", holder="h2") is False
        # 退出释放
        assert release_lease(c, lease_id="L", holder="h1") is True
        assert lease_status(c, "L") is None
        # 释放后可接管
        assert acquire_lease(c, lease_id="L", holder="h2", trade_date="20260810") is True


def test_expired_lease_can_be_taken_over(db_path, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(repo, "_now_timestamp", lambda: now[0])
    with sqlite3.connect(db_path) as c:
        assert acquire_lease(c, lease_id="L", holder="h1", trade_date="20260810",
                             ttl_seconds=300) is True
        # 推进到过期 → 他人可接管
        now[0] += 301
        assert acquire_lease(c, lease_id="L", holder="h2", trade_date="20260810",
                             ttl_seconds=300) is True
        assert lease_status(c, "L")["holder"] == "h2"


def test_concurrent_runners_single_lease(db_path):
    """同账户/交易日两个并发 runner，只有一个获得租约并产生成功 run。"""
    started = threading.Event()
    release = threading.Event()
    log: list[str] = []

    def blocking_step(**kwargs):
        log.append("a")
        started.set()
        assert release.wait(15)

    dag = DailyDag([StepSpec("a", "GLOBAL", "all", blocking_step)])
    runner_a = SchedulerRunner(db_path, dag, holder="runner-A")
    runner_b = SchedulerRunner(db_path, dag, holder="runner-B")
    results: dict[str, dict] = {}

    def run_a():
        results["a"] = runner_a.run_day("20260810")

    t = threading.Thread(target=run_a)
    t.start()
    assert started.wait(15), "runner A 未进入步骤"
    results["b"] = runner_b.run_day("20260810")
    release.set()
    t.join(15)
    assert results["b"]["status"] == "LEASE_CONFLICT", results["b"]
    assert results["a"]["status"] == "COMPLETED", results["a"]
    with sqlite3.connect(db_path) as c:
        runs = c.execute("SELECT status FROM dag_runs").fetchall()
    assert [r[0] for r in runs] == ["COMPLETED"]  # 只有一个成功 run


def test_lease_released_after_normal_run(db_path):
    """正常退出后租约释放，其他 holder 立即可用。"""
    dag = DailyDag([StepSpec("a", "GLOBAL", "all", lambda **kw: {})])
    runner = SchedulerRunner(db_path, dag, holder="r1")
    result = runner.run_day("20260810")
    assert result["status"] == "COMPLETED"
    with sqlite3.connect(db_path) as c:
        status = lease_status(c, repo.lease_id_for("20260810", "GLOBAL", "all"))
    assert status is None  # 租约已释放


def test_no_permanent_running_or_lease_after_crash(db_path, monkeypatch):
    """崩溃（不释放）后：无永久 RUNNING/租约；过期后可接管续跑。"""
    now = [1_000_000.0]
    monkeypatch.setattr(repo, "_now_timestamp", lambda: now[0])
    lease_id = repo.lease_id_for("20260810", "GLOBAL", "all")
    # 模拟崩溃遗留：持有租约 + RUNNING run + RUNNING step attempt
    with sqlite3.connect(db_path) as c:
        assert acquire_lease(c, lease_id=lease_id, holder="dead-runner",
                             trade_date="20260810", ttl_seconds=300) is True
        run_id = repo.start_run(c, trade_date="20260810")
        repo.record_step_attempt(
            c, run_id=run_id, trade_date="20260810", step_name="a",
            scope_type="GLOBAL", scope_id="all", input_hash="default",
            attempt=1, status="RUNNING",
        )
    # 崩溃后：run 状态 RUNNING、step RUNNING、租约存在但有过期时间（非永久）
    with sqlite3.connect(db_path) as c:
        assert c.execute("SELECT status FROM dag_runs").fetchone()[0] == "RUNNING"
        assert c.execute("SELECT status FROM dag_step_runs").fetchone()[0] == "RUNNING"
        st = lease_status(c, lease_id)
        assert st is not None and st["expired"] is False  # 租约不是永久

    # 租约过期后新 holder 可接管；RUNNING attempt 同号覆盖重试
    now[0] += 301
    log: list[str] = []

    def step_a(**kwargs):
        log.append("a")

    dag = DailyDag([StepSpec("a", "GLOBAL", "all", step_a)])
    runner = SchedulerRunner(db_path, dag, holder="new-runner")
    result = runner.run_day("20260810")
    assert result["status"] == "COMPLETED", result
    with sqlite3.connect(db_path) as c:
        attempts = c.execute(
            "SELECT attempt, status FROM dag_step_runs ORDER BY attempt"
        ).fetchall()
        runs = c.execute("SELECT status FROM dag_runs").fetchall()
        lease = lease_status(c, lease_id)
    assert [(r[0], r[1]) for r in attempts] == [(1, "SUCCESS")]  # RUNNING 覆盖重试
    assert [r[0] for r in runs] == ["COMPLETED"]
    assert lease is None  # 退出释放，无遗留
    assert log.count("a") == 1
