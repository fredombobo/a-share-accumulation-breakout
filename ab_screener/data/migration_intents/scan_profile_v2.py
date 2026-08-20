"""迁移意图：ScanProfile 与漏斗 run manifest（P4.2）。

- `scan_profiles`：版本化 profile（PK: profile_id, version；非账本投影）。
- `scan_funnel_runs`：不可变 run manifest（profile + input + stages + result hash）。
依赖 `v2:signals`（信号观察落库在同一套 schema 语义）。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:scan_profiles"


def apply_scan_profiles(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scan_profiles (
          profile_id TEXT NOT NULL,
          version TEXT NOT NULL,
          name TEXT NOT NULL,
          strategy_ids_json TEXT NOT NULL,
          configs_json TEXT NOT NULL,
          config_hash TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('DRAFT','ACTIVE','RETIRED')),
          created_at TEXT NOT NULL,
          PRIMARY KEY (profile_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_scan_profiles_status ON scan_profiles(status, created_at);

        CREATE TABLE IF NOT EXISTS scan_funnel_runs (
          run_manifest_id TEXT PRIMARY KEY,
          profile_id TEXT NOT NULL,
          profile_version TEXT NOT NULL,
          input_hash TEXT NOT NULL,
          stages_json TEXT NOT NULL,
          result_hash TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS trg_funnel_runs_append_only
          BEFORE UPDATE ON scan_funnel_runs
          BEGIN SELECT RAISE(ABORT, 'scan_funnel_runs is append-only'); END;
        """
    )


def register_scan_profile_migration() -> None:
    if getattr(register_scan_profile_migration, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_scan_profiles, depends_on=("v2:signals",))
    register_scan_profile_migration._registered = True  # type: ignore[attr-defined]


register_scan_profile_migration()
