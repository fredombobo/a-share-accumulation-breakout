"""v2 迁移注册表：统一注册所有迁移命名空间，禁止 MAX(schema_version) 跳过。

契约（implementation P0.4）：新增 schema_migrations_v2(migration_id, checksum,
applied_at, duration_ms)，字符串 ID/namespace 统一注册现有 paper 1–8、core 9–13、
logic 101+ 与 v2 迁移。Web 启动只 assert_schema_compatible()，不自动执行 DDL。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NAMESPACES = ("paper", "core", "logic", "v2")

# 迁移注册表：id -> {deps, apply}。apply 为纯函数（不写 schema_migrations_v2，由 runner 记录）。
# P0 阶段仅登记命名空间元数据；具体 DDL 迁移由各阶段实现后在此注册。
_REGISTRY: dict[str, dict] = {}
_INTENTS_LOADED = False

# The first production release used ``co_filename`` and ``co_firstlineno`` as
# its checksum input.  That made an unchanged migration appear modified when
# the checkout moved to another worktree.  These are the exact legacy values
# written by that release, each pinned to the stable source checksum expected
# during the one-way transition.  A later source change will therefore *not*
# be accepted merely because the database still contains a known legacy hash.
_LEGACY_CHECKSUM_COMPATIBILITY: dict[str, tuple[str, str]] = {
    "v2:aux_history": (
        "db22e80e7cdd9c98",
        "sha256:ae8b06792af34f11607d58b359137e612ca627a755314ac685357ccdd6dd2963",
    ),
    "v2:corporate_actions": (
        "7e04e70bb86ef50b",
        "sha256:0f552f48f90eced217c832c5e30f101bf3ed07b4e3a56deb32fe2843a054f8cf",
    ),
    "v2:execution_lineage": (
        "1b35fa03e104c000",
        "sha256:ddcf22c4508a27d9d0538e71d26af96e8da7b8b7b0bcbd69138536d7bc55cb86",
    ),
    "v2:instrument_rules": (
        "159d954eeb354661",
        "sha256:3bdb99b3415e0f285d0b47492af098c92f6a92bc473f597fda3da5ca1e1c8a49",
    ),
    "v2:operations": (
        "8a52183189d9e002",
        "sha256:59e14b921be5fa9fe6bfde0a956f41a85ec296508b65e42f462fac7fd276582a",
    ),
    "v2:pit_history": (
        "5d679ee799f7e4b3",
        "sha256:2fb53337f1ff2bff6537ff3edbb4b153924950e7683ccf72128672eec61b6203",
    ),
    "v2:portfolio_risk": (
        "289b762bd8dc9d59",
        "sha256:6f9af47509c77c32610be63d25c73b5353b01d4e74c14d09c485b11bce7d4edf",
    ),
    "v2:research_governance": (
        "928d12bf7acadb0c",
        "sha256:fc0516e63ffb78d5bdbab8e4e30f4119474d32be9c76815e25cae811324b9833",
    ),
    "v2:review": (
        "9ea13e5a14fb3e08",
        "sha256:fcab36a5aecaef7f0b1cdaa2c7f1a7cb4d461de7859d11cc733c33ed7fff62f8",
    ),
    "v2:scan_profiles": (
        "4060a78dc1df41e0",
        "sha256:b251bcdea8f6d8dbaa420da16b56ccd20e21dcf71604ad3c537696094a0e768c",
    ),
    "v2:signals": (
        "020a6fe663ada0a1",
        "sha256:68b7d926a5a13d0d368182b7f1d1293317acd6ff59b19a207fc0bd396200928c",
    ),
}


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


def applied_migrations(
    conn: sqlite3.Connection, *, create_table: bool = True
) -> dict[str, MigrationRecord]:
    if create_table:
        ensure_table(conn)
    elif conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='schema_migrations_v2'"
    ).fetchone() is None:
        return {}
    rows = conn.execute("SELECT migration_id, checksum, applied_at, duration_ms FROM schema_migrations_v2").fetchall()
    return {r[0]: MigrationRecord(r[0], r[1], r[2], r[3]) for r in rows}


def pending_migrations(conn: sqlite3.Connection) -> list[str]:
    _ensure_intents()
    applied = set(applied_migrations(conn))
    return [mid for mid in registered_ids() if mid not in applied]


def migration_checksum(migration_id: str) -> str:
    """Return a path-independent checksum of migration source and dependencies."""
    record = _REGISTRY[migration_id]
    fn = record["apply"]
    try:
        source = textwrap.dedent(inspect.getsource(fn))
        source = source.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    except (OSError, TypeError):
        # Dynamically-created test/plugin functions may not have retrievable
        # source.  The semantic code fields deliberately exclude filename,
        # first line number and line tables.
        code = getattr(fn, "__code__", None)
        if code is None:
            raise ValueError(f"迁移函数无可校验源码: {migration_id}")
        source = json.dumps(
            {
                "code": code.co_code.hex(),
                "consts": [repr(value) for value in code.co_consts],
                "names": list(code.co_names),
                "varnames": list(code.co_varnames),
                "freevars": list(code.co_freevars),
                "cellvars": list(code.co_cellvars),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    payload = {
        "migration_id": migration_id,
        "depends_on": list(record["depends_on"]),
        "source": source,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _checksum_matches(migration_id: str, stored: str) -> bool:
    expected = migration_checksum(migration_id)
    if stored == expected:
        return True
    legacy = _LEGACY_CHECKSUM_COMPATIBILITY.get(migration_id)
    return bool(legacy and stored == legacy[0] and expected == legacy[1])


def legacy_checksum_upgrades(conn: sqlite3.Connection) -> list[str]:
    """List recognized legacy checksums that can be safely upgraded."""
    upgrades: list[str] = []
    for migration_id, record in applied_migrations(conn).items():
        legacy = _LEGACY_CHECKSUM_COMPATIBILITY.get(migration_id)
        if legacy and record.checksum == legacy[0] and migration_checksum(migration_id) == legacy[1]:
            upgrades.append(migration_id)
    return sorted(upgrades)


def _checksum_drift(
    conn: sqlite3.Connection, *, create_table: bool = True
) -> list[str]:
    drift: list[str] = []
    for migration_id, record in applied_migrations(
        conn, create_table=create_table
    ).items():
        if migration_id not in _REGISTRY:
            drift.append(f"{migration_id}: 未注册迁移")
        elif not _checksum_matches(migration_id, record.checksum):
            drift.append(f"{migration_id}: checksum 漂移")
    return drift


def _upgrade_legacy_checksums(conn: sqlite3.Connection) -> list[str]:
    upgrades = legacy_checksum_upgrades(conn)
    for migration_id in upgrades:
        conn.execute(
            "UPDATE schema_migrations_v2 SET checksum=? WHERE migration_id=?",
            (migration_checksum(migration_id), migration_id),
        )
    if upgrades:
        conn.commit()
    return upgrades


def plan_migrations(conn: sqlite3.Connection) -> dict:
    """返回 {pending, already_applied, total}（--plan 输出）。"""
    return {
        "pending": pending_migrations(conn),
        "already_applied": sorted(applied_migrations(conn)),
        "registered_total": len(registered_ids()),
    }


def apply_pending(conn: sqlite3.Connection, *, dry_run: bool = False) -> list[str]:
    """按依赖顺序应用未应用迁移。dry_run=True 只返回将执行列表。"""
    drift = _checksum_drift(conn)
    if drift:
        raise RuntimeError("已应用迁移 checksum 不匹配，拒绝继续: " + "; ".join(drift))
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

    _upgrade_legacy_checksums(conn)

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
        _ensure_intents()
        applied = applied_migrations(conn, create_table=False)
        registered = set(registered_ids())
        pending = [mid for mid in registered_ids() if mid not in applied]
        drift = []
        for mid, rec in applied.items():
            # 账本里可能留有已退役的 migration id，以及历史版本写入的
            # ``sha256:<64 hex>`` 内容哈希。它们是不可变的历史事实：既不该让
            # 启动校验崩溃，也不能被新版 16 位算法误报为漂移。
            if mid not in registered:
                continue
            if rec.checksum.startswith("sha256:") and len(rec.checksum) == 71:
                continue
            if not _checksum_matches(mid, rec.checksum):
                drift.append(f"{mid}: checksum 漂移")
        issues = [f"MIGRATION_PENDING:{m}" for m in pending] + drift
        return (not issues), issues
    finally:
        conn.close()
