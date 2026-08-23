"""迁移意图：公司行为 PIT 元数据列（V2R-D）。

在既有 `corporate_actions` 账本上追加 PIT 语义列：
- `effective_at`：事件生效时点（默认取 ex_date，按 +08:00 归一化）。
- `ingested_at`：本地入库时点。
- `revision`：同一业务键（ts_code, ex_date, kind）的修订号，幂等入账时沿用既有 revision。

依赖 `v2:corporate_actions`（账本/状态投影先就绪）。仅 ALTER 加列，不改账本行内容。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:corporate_action_pit"


def apply_corporate_action_pit(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(corporate_actions)").fetchall()}
    if "effective_at" not in cols:
        conn.execute("ALTER TABLE corporate_actions ADD COLUMN effective_at TEXT")
    if "ingested_at" not in cols:
        conn.execute("ALTER TABLE corporate_actions ADD COLUMN ingested_at TEXT")
    if "revision" not in cols:
        conn.execute("ALTER TABLE corporate_actions ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")


def register_corporate_action_pit_migration() -> None:
    if getattr(register_corporate_action_pit_migration, "_registered", False):
        return
    register_migration(
        _MIGRATION_ID, apply_corporate_action_pit, depends_on=("v2:corporate_actions",)
    )
    register_corporate_action_pit_migration._registered = True  # type: ignore[attr-defined]


register_corporate_action_pit_migration()
