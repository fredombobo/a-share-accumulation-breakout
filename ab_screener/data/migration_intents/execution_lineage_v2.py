"""迁移意图：执行血缘固化（P2.3）。

pt_fill 新增执行血缘列：fee_breakdown、rule/cost/fill model version、
participation bps、quote available_at、input hash。
- 历史成交不重写；异常时停止新撮合，以 model version 区分；现金流水不回滚。
- 本迁移与 paper_trading/migrations.M009 幂等对齐（两入口任一路径先跑均可）。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:execution_lineage"

LINEAGE_COLUMNS: dict[str, str] = {
    "other_fee_fen": "INTEGER NOT NULL DEFAULT 0 CHECK (other_fee_fen >= 0)",
    "fee_breakdown_json": "TEXT NOT NULL DEFAULT '{}'",
    "cost_version": "TEXT NOT NULL DEFAULT 'legacy-v1'",
    "participation_bps": "INTEGER NOT NULL DEFAULT 500",
    "quote_available_at": "TEXT NOT NULL DEFAULT ''",
    "input_hash": "TEXT NOT NULL DEFAULT ''",
    "rule_version": "TEXT NOT NULL DEFAULT 'v1'",
}


def apply_execution_lineage(conn: sqlite3.Connection) -> None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pt_fill'"
    ).fetchone()
    if not has:
        return
    have = {r[1] for r in conn.execute("PRAGMA table_info(pt_fill)").fetchall()}
    for name, definition in LINEAGE_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE pt_fill ADD COLUMN {name} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_fill_input_hash ON pt_fill(input_hash)")


def register_execution_lineage_migration() -> None:
    if getattr(register_execution_lineage_migration, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_execution_lineage)
    register_execution_lineage_migration._registered = True  # type: ignore[attr-defined]


register_execution_lineage_migration()
