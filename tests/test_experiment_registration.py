"""P3.1 实验注册测试：幂等、核心字段不可变、失败/取消/拒绝登记。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending, registered_ids
from ab_screener.research.registry import (
    ResearchGovernanceError,
    register_experiment,
    register_trial,
    require_experiment,
    transition_experiment_status,
)


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "gov.db"))
    apply_pending(c)
    yield c
    c.close()


def test_register_experiment_idempotent(conn):
    e1 = register_experiment(conn, strategy="A", params={"vol_ratio": 1.5},
                             config_hash="cfg1")
    e2 = register_experiment(conn, strategy="A", params={"vol_ratio": 1.5},
                             config_hash="cfg1")
    assert e1 == e2
    # 参数不同 → 不同实验
    e3 = register_experiment(conn, strategy="A", params={"vol_ratio": 1.6},
                             config_hash="cfg1")
    assert e3 != e1


def test_experiment_core_fields_immutable(conn):
    eid = register_experiment(conn, strategy="A", params={"vol_ratio": 1.5},
                              config_hash="cfg1")
    with pytest.raises(Exception, match="immutable"):
        conn.execute(
            "UPDATE experiment_registrations SET strategy='B' WHERE experiment_id=?",
            (eid,),
        )
    conn.rollback()
    # 状态推进合法
    transition_experiment_status(conn, eid, "RUNNING")
    transition_experiment_status(conn, eid, "COMPLETED")
    with pytest.raises(ResearchGovernanceError, match="非法实验状态"):
        transition_experiment_status(conn, eid, "BOGUS")


def test_failed_cancelled_rejected_trials_registered(conn):
    eid = register_experiment(conn, strategy="A", params={"vol_ratio": 1.5},
                              config_hash="cfg1")
    ok = register_trial(conn, experiment_id=eid, params={"vol_ratio": 1.5},
                        status="COMPLETED", outcome={"net_pf": 1.2})
    fail = register_trial(conn, experiment_id=eid, params={"vol_ratio": 1.6},
                          status="FAILED", outcome={"reason": "no data"})
    cancelled = register_trial(conn, experiment_id=eid, params={"vol_ratio": 1.7},
                               status="CANCELLED")
    rejected = register_trial(conn, experiment_id=eid, params={"vol_ratio": 1.8},
                              status="REJECTED", outcome={"reason": "overfit"})
    assert len({ok, fail, cancelled, rejected}) == 4
    # 全部可见（不静默丢弃）
    rows = conn.execute(
        "SELECT status FROM research_trials WHERE experiment_id=? ORDER BY status",
        (eid,),
    ).fetchall()
    statuses = sorted(r[0] for r in rows)
    assert statuses == ["CANCELLED", "COMPLETED", "FAILED", "REJECTED"]


def test_trial_requires_registered_experiment(conn):
    with pytest.raises(ResearchGovernanceError, match="未注册"):
        register_trial(conn, experiment_id="NOPE", params={}, status="PENDING")


def test_require_experiment_fail_closed(conn):
    with pytest.raises(ResearchGovernanceError, match="不存在"):
        require_experiment(conn, "NOPE")


def test_migration_registered():
    assert "v2:research_governance" in registered_ids()


def test_missing_table_fail_closed(tmp_path: Path):
    empty = sqlite3.connect(str(tmp_path / "naked.db"))
    try:
        with pytest.raises(ResearchGovernanceError, match="表不存在"):
            register_experiment(empty, strategy="A", params={}, config_hash="c")
    finally:
        empty.close()
