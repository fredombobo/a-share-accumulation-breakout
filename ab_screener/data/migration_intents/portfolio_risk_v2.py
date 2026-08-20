"""迁移意图：风险快照（P5.2）。

- `risk_snapshots`：append-only 风险快照（行情/规则/配置版本 + 指标 JSON）。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:portfolio_risk"


def apply_portfolio_risk(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS risk_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          trade_date TEXT NOT NULL,
          account_id INTEGER NOT NULL DEFAULT 1,
          market_version TEXT NOT NULL,
          rule_version TEXT NOT NULL,
          config_version TEXT NOT NULL,
          metrics_json TEXT NOT NULL,
          scenarios_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_risk_snap_date ON risk_snapshots(trade_date, created_at);
        CREATE TRIGGER IF NOT EXISTS trg_risk_snap_append_only
          BEFORE UPDATE ON risk_snapshots
          BEGIN SELECT RAISE(ABORT, 'risk_snapshots is append-only'); END;
        """
    )


def register_portfolio_risk_migration() -> None:
    if getattr(register_portfolio_risk_migration, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_portfolio_risk)
    register_portfolio_risk_migration._registered = True  # type: ignore[attr-defined]


register_portfolio_risk_migration()
