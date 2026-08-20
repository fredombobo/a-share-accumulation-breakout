"""迁移意图：不可变信号、事件、生命周期投影与 outcome（P4.3）。

- `signal_observations`：append-only 原始发现（不可变；同键重跑幂等）。
- `signal_events`：append-only 人工/系统事件。
- `signal_lifecycle_projection`：可重建投影（OBSERVED→QUALIFIED→WATCHING|TRADEABLE→
  ORDER_CREATED→ENTERED；ENTERED 只由 fill 触发）。
- `signal_outcomes`：5/10/20 日成熟结果，修订追加版本（收益 NULL 表示未成交，不填 0）。
依赖 `v2:pit_history`。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:signals"


def apply_signals(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS signal_observations (
          observation_id TEXT PRIMARY KEY,
          strategy_definition_id TEXT NOT NULL,
          strategy_hash TEXT NOT NULL,
          input_hash TEXT NOT NULL,
          snapshot_id TEXT NOT NULL,
          ts_code TEXT NOT NULL,
          signal_date TEXT NOT NULL,
          config_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          explanation TEXT NOT NULL,
          tradeable INTEGER NOT NULL,
          entry_definition_id TEXT NOT NULL,
          observed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sigobs_strategy
          ON signal_observations(strategy_definition_id, signal_date);
        CREATE TRIGGER IF NOT EXISTS trg_sigobs_immutable
          BEFORE UPDATE ON signal_observations
          BEGIN SELECT RAISE(ABORT, 'signal_observations is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS trg_sigobs_no_delete
          BEFORE DELETE ON signal_observations
          BEGIN SELECT RAISE(ABORT, 'signal_observations is append-only'); END;

        CREATE TABLE IF NOT EXISTS signal_events (
          event_id TEXT PRIMARY KEY,
          observation_id TEXT NOT NULL REFERENCES signal_observations(observation_id),
          event_type TEXT NOT NULL,
          actor TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          occurred_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sigevents_obs ON signal_events(observation_id);

        CREATE TABLE IF NOT EXISTS signal_lifecycle_projection (
          observation_id TEXT PRIMARY KEY,
          status TEXT NOT NULL CHECK (status IN
            ('OBSERVED','QUALIFIED','WATCHING','TRADEABLE','ORDER_CREATED','ENTERED','RETIRED')),
          updated_at TEXT NOT NULL,
          manual_exercise INTEGER NOT NULL DEFAULT 0,
          order_id TEXT
        );

        CREATE TABLE IF NOT EXISTS signal_outcomes (
          outcome_id TEXT PRIMARY KEY,
          observation_id TEXT NOT NULL REFERENCES signal_observations(observation_id),
          horizon_days INTEGER NOT NULL CHECK (horizon_days IN (5,10,20)),
          revision INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL CHECK (status IN
            ('PENDING','MATURED','UNFILLABLE','EXPIRED')),
          entry_price_micro INTEGER,
          exit_price_micro INTEGER,
          net_return REAL,
          benchmark_excess REAL,
          available_at TEXT NOT NULL,
          UNIQUE (observation_id, horizon_days, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_sigout_obs ON signal_outcomes(observation_id, horizon_days);
        """
    )


def register_signal_migrations() -> None:
    if getattr(register_signal_migrations, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_signals, depends_on=("v2:pit_history",))
    register_signal_migrations._registered = True  # type: ignore[attr-defined]


register_signal_migrations()
