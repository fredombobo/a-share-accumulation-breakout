"""迁移意图：公司行为账本（P1.3）。

- `corporate_actions`：append-only 事件账本（SPLIT/DIVIDEND/RIGHTS/REVERSAL）。
  账本行内容（含 reversal_of）只在 INSERT 时写入，禁止 UPDATE/DELETE（触发器兜底）。
- `corporate_action_status`：可变投影（action_id → status），仅状态推进用；
  更正 = 追加 REVERSAL 事件 + 投影标记原事件 REVERSED。
依赖 `v2:pit_history`。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:corporate_actions"


def apply_corporate_actions(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS corporate_actions (
          corporate_action_id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts_code TEXT NOT NULL,
          ex_date TEXT NOT NULL,
          kind TEXT NOT NULL CHECK (kind IN
            ('SPLIT','DIVIDEND','RIGHTS','REVERSAL')),
          payload_json TEXT NOT NULL,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          checksum TEXT NOT NULL,
          reversal_of INTEGER,
          UNIQUE (ts_code, ex_date, kind, checksum)
        );
        CREATE INDEX IF NOT EXISTS idx_ca_ts ON corporate_actions(ts_code, ex_date);
        CREATE TRIGGER IF NOT EXISTS trg_corporate_actions_no_update
          BEFORE UPDATE ON corporate_actions
          BEGIN SELECT RAISE(ABORT, 'corporate_actions is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS trg_corporate_actions_no_delete
          BEFORE DELETE ON corporate_actions
          BEGIN SELECT RAISE(ABORT, 'corporate_actions is append-only'); END;

        CREATE TABLE IF NOT EXISTS corporate_action_status (
          corporate_action_id INTEGER PRIMARY KEY,
          status TEXT NOT NULL CHECK (status IN ('PENDING','APPLIED','REVERSED')),
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cas_status ON corporate_action_status(status);
        """
    )


def register_corporate_action_migrations() -> None:
    if getattr(register_corporate_action_migrations, "_registered", False):
        return
    register_migration(
        _MIGRATION_ID, apply_corporate_actions, depends_on=("v2:pit_history",)
    )
    register_corporate_action_migrations._registered = True  # type: ignore[attr-defined]


register_corporate_action_migrations()
