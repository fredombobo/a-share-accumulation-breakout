"""迁移意图：PIT 历史版本表（P1.1）。

仅注册空表迁移（append-only history/projection），DDL 与 517 万行历史回填分开：
schema steward 先注册本迁移，回填由 scripts/backfill_pit_v2.py 按分块/checkpoint 执行。

历史表约束：
- 一律 append-only：禁止 UPDATE/DELETE（触发器兜底）。
- 业务键 + revision 主键；`available_at` 为 +08:00 文本（可排序）。
- `content_hash` 供抽样核对；`source` 记录数据来源。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

# 历史表定义：dataset -> 业务键列（字符串化）。与 data_point.PitRecord 对齐。
HISTORY_TABLES: dict[str, list[str]] = {
    "daily_history": ["ts_code", "trade_date"],
    "daily_basic_history": ["ts_code", "trade_date"],
    "moneyflow_history": ["ts_code", "trade_date"],
    "fina_indicator_history": ["ts_code", "ann_date"],
    "stock_basic_history": ["ts_code"],
    "adj_factor_history": ["ts_code", "trade_date"],
}

_MIGRATION_ID = "v2:pit_history"


def _ddl_for(table: str, key_cols: list[str]) -> str:
    pk_cols = ", ".join(key_cols + ["revision"])
    return f"""
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


def apply_pit_history(conn: sqlite3.Connection) -> None:
    """创建全部 PIT 历史空表 + 追加写入清单表 + 回填 checkpoint 表。"""
    for table, key_cols in HISTORY_TABLES.items():
        conn.executescript(_ddl_for(table, key_cols))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS raw_ingest_manifests (
          manifest_id TEXT PRIMARY KEY,
          dataset TEXT NOT NULL,
          partition_key TEXT NOT NULL,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          row_count INTEGER NOT NULL,
          content_sha256 TEXT NOT NULL,
          ingested_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rim_dataset
          ON raw_ingest_manifests(dataset, partition_key);

        CREATE TABLE IF NOT EXISTS pit_backfill_checkpoints (
          dataset TEXT NOT NULL,
          partition_key TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('done','in_progress')),
          last_key TEXT,
          row_count INTEGER NOT NULL DEFAULT 0,
          source_hash TEXT,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (dataset, partition_key)
        );
        """
    )


def register_pit_migrations() -> None:
    """注册 P1.1 迁移（幂等：重复调用不抛错，由注册表去重守卫）。"""
    # register_migration 会拒绝重复 id；用模块级标志保证单次注册
    if getattr(register_pit_migrations, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_pit_history)
    register_pit_migrations._registered = True  # type: ignore[attr-defined]


register_pit_migrations()
