"""迁移意图：instrument 宇宙规则（P1.2）。

- `instrument_universe_rules`：当前宇宙规则投影（可 upsert 白名单字段；非账本）。
  命名避开 paper_trading.instrument_rules（成本/执行规则表）避免同名冲突。
- `instrument_lifecycle_history`：规则变更的 PIT append-only 历史（与 pit_history 同构）。
依赖 `v2:pit_history`（同套 append-only 语义）。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:instrument_rules"


def apply_instrument_rules(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS instrument_universe_rules (
          ts_code TEXT PRIMARY KEY,
          name TEXT NOT NULL DEFAULT '',
          exchange TEXT NOT NULL DEFAULT '',
          security_type TEXT NOT NULL CHECK (security_type IN
            ('stock','index','etf','fund','bond','bse','other')),
          list_date TEXT NOT NULL,
          delist_date TEXT,
          source TEXT NOT NULL DEFAULT 'tushare',
          updated_at TEXT NOT NULL,
          checksum TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_instrument_universe_type
          ON instrument_universe_rules(security_type, list_date);

        CREATE TABLE IF NOT EXISTS instrument_lifecycle_history (
          ts_code TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          available_at TEXT NOT NULL,
          source TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          PRIMARY KEY (ts_code, revision)
        );
        CREATE TRIGGER IF NOT EXISTS trg_instrument_lifecycle_no_update
          BEFORE UPDATE ON instrument_lifecycle_history
          BEGIN SELECT RAISE(ABORT, 'instrument_lifecycle_history is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS trg_instrument_lifecycle_no_delete
          BEFORE DELETE ON instrument_lifecycle_history
          BEGIN SELECT RAISE(ABORT, 'instrument_lifecycle_history is append-only'); END;
        """
    )


def register_instrument_migrations() -> None:
    if getattr(register_instrument_migrations, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_instrument_rules, depends_on=("v2:pit_history",))
    register_instrument_migrations._registered = True  # type: ignore[attr-defined]


register_instrument_migrations()
