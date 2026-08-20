"""v2 迁移注册表：统一注册所有迁移命名空间，禁止 MAX(schema_version) 跳过。

契约（implementation P0.4）：新增 schema_migrations_v2(migration_id, checksum,
applied_at, duration_ms)，字符串 ID/namespace 统一注册现有 paper 1–8、core 9–13、
logic 101+ 与 v2 迁移。Web 启动只 assert_schema_compatible()，不自动执行 DDL。
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NAMESPACES = ("paper", "core", "logic", "v2")

# 迁移注册表：id -> {deps, apply}。apply 为纯函数（不写 schema_migrations_v2，由 runner 记录）。
# P0 阶段仅登记命名空间元数据；具体 DDL 迁移由各阶段实现后在此注册。
_REGISTRY: dict[str, dict] = {}
_INTENTS_LOADED = False


def _ensure_intents() -> None:
    """导入全部迁移意图包（side-effect 注册）。registry 自完备，不依赖调用方顺序。"""
    global _INTENTS_LOADED
    if _INTENTS_LOADED:
        return
    import ab_screener.data.migration_intents  # noqa: F401  意图包导入即注册

    _INTENTS_LOADED = True


@dataclass
class MigrationRecord:
    migration_id: str
    checksum: str
    applied_at: str | None
    duration_ms: int | None


def register_migration(migration_id: str, apply_fn: Callable[[sqlite3.Connection], None],
                       depends_on: tuple[str, ...] = ()) -> None:
    """注册一个迁移（TDD：先注册与测试，后实现 DDL）。"""
    if ":" not in migration_id or migration_id.split(":", 1)[0] not in NAMESPACES:
        raise ValueError(f"migration_id 必须为 namespace:name 形式: {migration_id}")
    if migration_id in _REGISTRY:
        raise ValueError(f"重复注册迁移: {migration_id}")
    _REGISTRY[migration_id] = {"apply": apply_fn, "depends_on": depends_on}


def registered_ids() -> list[str]:
    _ensure_intents()
    return sorted(_REGISTRY.keys())


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations_v2 ("
        " migration_id TEXT PRIMARY KEY,"
        " checksum TEXT NOT NULL,"
        " applied_at TEXT,"
        " duration_ms INTEGER)"
    )


def applied_migrations(conn: sqlite3.Connection) -> dict[str, MigrationRecord]:
    ensure_table(conn)
    rows = conn.execute("SELECT migration_id, checksum, applied_at, duration_ms FROM schema_migrations_v2").fetchall()
    return {r[0]: MigrationRecord(r[0], r[1], r[2], r[3]) for r in rows}


def pending_migrations(conn: sqlite3.Connection) -> list[str]:
    _ensure_intents()
    applied = set(applied_migrations(conn))
    return [mid for mid in registered_ids() if mid not in applied]


def migration_checksum(migration_id: str) -> str:
    """迁移定义哈希：同 id 定义变化 → checksum 变（防篡改/漂移）。"""
    fn = _REGISTRY[migration_id]["apply"]
    src = getattr(fn, "__code__", None)
    if src is None:
        return ""
    blob = f"{migration_id}::{src.co_filename}::{src.co_firstlineno}".encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def plan_migrations(conn: sqlite3.Connection) -> dict:
    """返回 {pending, already_applied, total}（--plan 输出）。"""
    return {
        "pending": pending_migrations(conn),
        "already_applied": sorted(applied_migrations(conn)),
        "registered_total": len(registered_ids()),
    }


def apply_pending(conn: sqlite3.Connection, *, dry_run: bool = False) -> list[str]:
    """按依赖顺序应用未应用迁移。dry_run=True 只返回将执行列表。"""
    applied = set(applied_migrations(conn))
    order: list[str] = []
    visited: set[str] = set()

    def visit(mid: str) -> None:
        if mid in visited or mid in applied:
            return
        visited.add(mid)
        for dep in _REGISTRY[mid]["depends_on"]:
            if dep not in _REGISTRY:
                raise ValueError(f"迁移依赖未注册: {dep}")
            visit(dep)
        order.append(mid)

    for mid in registered_ids():
        visit(mid)
    if dry_run:
        return order

    applied_now: list[str] = []
    for mid in order:
        if mid in applied:
            continue
        t0 = time.monotonic()
        _REGISTRY[mid]["apply"](conn)
        checksum = migration_checksum(mid)
        conn.execute(
            "INSERT INTO schema_migrations_v2 (migration_id, checksum, applied_at, duration_ms)"
            " VALUES (?,?,datetime('now'),?)"
            " ON CONFLICT(migration_id) DO UPDATE SET checksum=excluded.checksum,"
            " applied_at=excluded.applied_at, duration_ms=excluded.duration_ms",
            (mid, checksum, int((time.monotonic() - t0) * 1000)),
        )
        conn.commit()
        applied_now.append(mid)
    return applied_now


def schema_compatible(db_path: str | Path) -> tuple[bool, list[str]]:
    """assert_schema_compatible 用：未应用迁移或 checksum 漂移 → 不兼容。"""
    db_path = Path(db_path)
    if not db_path.is_file():
        return False, ["DB_MISSING"]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        pending = pending_migrations(conn)
        applied = applied_migrations(conn)
        drift = []
        for mid, rec in applied.items():
            if rec.checksum != migration_checksum(mid):
                drift.append(f"{mid}: checksum 漂移")
        issues = [f"MIGRATION_PENDING:{m}" for m in pending] + drift
        return (not issues), issues
    finally:
        conn.close()
