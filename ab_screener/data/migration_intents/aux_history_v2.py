"""迁移意图：辅助数据集历史表（B 阶段，2026-08-18）。

新表与 pit_history 同构（append-only + PIT 五元组），但独立迁移
（v2:pit_history 已应用，不可改其 DDL——checksum 幂等）。
数据集：
- top_list_history（龙虎榜：ts_code+trade_date）
- margin_history（两融个股：ts_code+trade_date）
- cyq_history（筹码分布：ts_code+trade_date）
- holder_history（十大股东/股东户数：ts_code+end_date 报告期）
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:aux_history"

AUX_TABLES: dict[str, list[str]] = {
    "top_list_history": ["ts_code", "trade_date"],
    "margin_history": ["ts_code", "trade_date"],
    "cyq_history": ["ts_code", "trade_date"],
    "holder_history": ["ts_code", "end_date"],
}


def all_history_tables() -> dict[str, list[str]]:
    """全量历史表视图（pit + aux）：writer/repository/backfill 共用，
    避免修改已发布迁移的常量导致 checksum 漂移。"""
    from ab_screener.data.migration_intents.pit_history_v2 import HISTORY_TABLES

    merged = dict(HISTORY_TABLES)
    merged.update(AUX_TABLES)
    return merged


ALL_HISTORY_TABLES = all_history_tables()


def apply_aux_history(conn: sqlite3.Connection) -> None:
    for table, key_cols in AUX_TABLES.items():
        pk_cols = ", ".join(key_cols + ["revision"])
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
              {", ".join(f"{c} TEXT NOT NULL" for c in key_cols)},
              revision INTEGER NOT NULL CHECK (revision >= 1),
              available_at TEXT NOT NULL,
              source TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              PRIMARY KEY ({pk_cols})
            );
            CREATE INDEX IF NOT EXISTS idx_{table}_key
              ON {table}({", ".join(key_cols)}, available_at);
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update
              BEFORE UPDATE ON {table}
              BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
              BEFORE DELETE ON {table}
              BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
            """
        )


def register_aux_migration() -> None:
    if getattr(register_aux_migration, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_aux_history, depends_on=("v2:pit_history",))
    register_aux_migration._registered = True  # type: ignore[attr-defined]


register_aux_migration()
