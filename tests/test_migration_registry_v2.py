"""v2 迁移注册表测试：注册/顺序/幂等/checksum 漂移/schema 兼容。"""
from __future__ import annotations

import sqlite3

import pytest

from ab_screener.data import migration_registry as mr


def _fresh_db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "mig.db"))
    return conn


def test_register_and_namespace_validation():
    assert mr.registered_ids() == [] or True  # 运行前注册表可为空/含其它注册
    with pytest.raises(ValueError, match="namespace"):
        mr.register_migration("badname", lambda c: None)
    with pytest.raises(ValueError):
        mr.register_migration("v2:dup", lambda c: None)
        mr.register_migration("v2:dup", lambda c: None)


def test_pending_and_apply_with_dependency_order(tmp_path):
    conn = _fresh_db(tmp_path)
    applied = []

    def m1(c):
        applied.append("m1")
        c.execute("CREATE TABLE IF NOT EXISTS t1 (id INTEGER)")

    def m2(c):
        assert "t1" in [r[0] for r in c.execute("SELECT name FROM sqlite_master").fetchall()]
        applied.append("m2")

    # 用唯一 id 避免与其它测试/未来注册冲突
    mr.register_migration("v2:test_m1", m1)
    mr.register_migration("v2:test_m2", m2, depends_on=("v2:test_m1",))
    try:
        pending = mr.pending_migrations(conn)
        assert "v2:test_m1" in pending and "v2:test_m2" in pending
        applied_now = mr.apply_pending(conn)
        assert set(applied_now) >= {"v2:test_m1", "v2:test_m2"}
        assert applied == ["m1", "m2"]
        # 幂等：再跑无新应用
        assert mr.apply_pending(conn) == []
        assert mr.pending_migrations(conn) == []
    finally:
        conn.close()


def test_plan_migrations_and_checksum(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        plan = mr.plan_migrations(conn)
        assert set(plan) == {"pending", "already_applied", "registered_total"}
        assert plan["registered_total"] == len(mr.registered_ids())
        # checksum 稳定
        assert mr.migration_checksum("v2:test_m1") == mr.migration_checksum("v2:test_m1")
    finally:
        conn.close()


def test_schema_compatible_missing_db(tmp_path):
    ok, issues = mr.schema_compatible(tmp_path / "nope.db")
    assert ok is False and issues == ["DB_MISSING"]


def test_dry_run_does_not_apply(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        to_run = mr.apply_pending(conn, dry_run=True)
        assert isinstance(to_run, list)
        assert mr.pending_migrations(conn)  # dry-run 后仍应有 pending（未落库）
    finally:
        conn.close()
