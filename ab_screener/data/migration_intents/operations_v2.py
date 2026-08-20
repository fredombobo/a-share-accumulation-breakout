"""迁移意图：运营（DAG/租约/审计/告警）（P6）。

- `dag_runs` / `dag_step_runs`：持久每日 DAG 与步骤 attempt（保留每次 attempt）。
- `dag_leases`：调度租约（防并发）。
- `audit_events`：append-only 全站审计（hash chain：prev_hash 链接）。
- `alert_events`：事件化告警（幂等键去重）。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:operations"


def apply_operations(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dag_runs (
          run_id TEXT PRIMARY KEY,
          trade_date TEXT NOT NULL,
          mode TEXT NOT NULL DEFAULT 'EOD',
          status TEXT NOT NULL CHECK (status IN
            ('PENDING','RUNNING','COMPLETED','FAILED')),
          created_at TEXT NOT NULL,
          finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dag_runs_date ON dag_runs(trade_date, status);

        CREATE TABLE IF NOT EXISTS dag_step_runs (
          step_run_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES dag_runs(run_id),
          trade_date TEXT NOT NULL,
          step_name TEXT NOT NULL,
          scope_type TEXT NOT NULL,
          scope_id TEXT NOT NULL,
          input_hash TEXT NOT NULL,
          attempt INTEGER NOT NULL,
          status TEXT NOT NULL CHECK (status IN
            ('PENDING','RUNNING','SUCCESS','FAIL','ATTEMPT_FAILED')),
          started_at TEXT,
          finished_at TEXT,
          error TEXT,
          UNIQUE (trade_date, step_name, scope_type, scope_id, input_hash, attempt)
        );
        CREATE INDEX IF NOT EXISTS idx_dag_steps_run ON dag_step_runs(run_id, step_name);

        CREATE TABLE IF NOT EXISTS dag_leases (
          lease_id TEXT PRIMARY KEY,
          holder TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_events (
          event_id TEXT PRIMARY KEY,
          actor TEXT NOT NULL,
          action TEXT NOT NULL,
          request_json TEXT NOT NULL,
          correlation_id TEXT NOT NULL,
          before_json TEXT,
          after_json TEXT,
          event_hash TEXT NOT NULL,
          prev_hash TEXT NOT NULL,
          occurred_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_chain ON audit_events(event_id, prev_hash);

        CREATE TABLE IF NOT EXISTS alert_events (
          alert_id TEXT PRIMARY KEY,
          alert_type TEXT NOT NULL,
          source TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          severity TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          dedupe_key TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL
        );
        """
    )


def register_operations_migration() -> None:
    if getattr(register_operations_migration, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_operations)
    register_operations_migration._registered = True  # type: ignore[attr-defined]


register_operations_migration()
