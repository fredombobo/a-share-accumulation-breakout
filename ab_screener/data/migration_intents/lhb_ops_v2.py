"""迁移意图：龙虎榜运维旁路表（T11）。

新 id `v2:lhb_ops`，不改 `v2:lhb_tracking` / `v2:operations` 已发布 intent。
告警投递状态机与 DAG 复用现有 `dag_runs`/`dag_leases`（mode=LHB_EOD），
本迁移只追加投递 ACK 表。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:lhb_ops"
DELIVERY_STATUS = ("CREATED", "SENT", "ACKED", "FAILED", "DEAD_LETTER")


def _append_only_triggers(table: str) -> str:
    return f"""
    CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update
      BEFORE UPDATE ON {table}
      BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
      BEFORE DELETE ON {table}
      BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
    """


def apply_lhb_ops(conn: sqlite3.Connection) -> None:
    statuses = "(" + ",".join("'" + s + "'" for s in DELIVERY_STATUS) + ")"
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS lhb_alert_delivery (
          delivery_id TEXT NOT NULL,
          alert_id TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK (revision >= 1),
          status TEXT NOT NULL CHECK (status IN {statuses}),
          attempt INTEGER NOT NULL CHECK (attempt >= 0),
          channel TEXT NOT NULL,
          dry_run INTEGER NOT NULL DEFAULT 1 CHECK (dry_run IN (0, 1)),
          last_error TEXT,
          source TEXT NOT NULL,
          available_at TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{{}}',
          PRIMARY KEY (delivery_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_alert_delivery
          ON lhb_alert_delivery(alert_id, status, revision);
        {_append_only_triggers("lhb_alert_delivery")}
        """
    )


def register_lhb_ops_migration() -> None:
    if getattr(register_lhb_ops_migration, "_registered", False):
        return
    register_migration(
        _MIGRATION_ID,
        apply_lhb_ops,
        depends_on=("v2:lhb_tracking", "v2:operations"),
    )
    register_lhb_ops_migration._registered = True  # type: ignore[attr-defined]


# 同 lhb_tracking_v2：不在导入时自注册，改由 register_lhb_intents() 显式开启。
