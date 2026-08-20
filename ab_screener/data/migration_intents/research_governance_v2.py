"""迁移意图：研究治理（P3.1）。

- `experiment_registrations`：实验注册表；注册后核心字段（strategy/params/config_hash）
  不可修改（触发器 ABORT）。失败/取消/被拒绝的状态照常登记。
- `research_trials`：trial 账本（append-only；FAILED/CANCELLED/REJECTED 同登记）。
- `research_artifacts`：产物登记（content_sha256 防篡改核对）。
- `promotion_decisions`：晋级决策账本。
依赖 `v2:pit_history`。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:research_governance"


def apply_research_governance(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS experiment_registrations (
          experiment_id TEXT PRIMARY KEY,
          strategy TEXT NOT NULL,
          params_json TEXT NOT NULL,
          config_hash TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'REGISTERED' CHECK (status IN
            ('REGISTERED','RUNNING','COMPLETED','CANCELLED','REJECTED')),
          registered_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS trg_experiment_core_immutable
          BEFORE UPDATE OF experiment_id, strategy, params_json, config_hash
          ON experiment_registrations
          BEGIN SELECT RAISE(ABORT, 'experiment core fields are immutable'); END;

        CREATE TABLE IF NOT EXISTS research_trials (
          trial_id TEXT PRIMARY KEY,
          experiment_id TEXT NOT NULL REFERENCES experiment_registrations(experiment_id),
          params_json TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN
            ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED','REJECTED')),
          outcome_json TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trials_exp ON research_trials(experiment_id, status);
        CREATE TRIGGER IF NOT EXISTS trg_trials_append_only
          BEFORE UPDATE OF trial_id, experiment_id, params_json ON research_trials
          BEGIN SELECT RAISE(ABORT, 'trial core fields are append-only'); END;

        CREATE TABLE IF NOT EXISTS research_artifacts (
          artifact_id TEXT PRIMARY KEY,
          trial_id TEXT NOT NULL REFERENCES research_trials(trial_id),
          artifact_type TEXT NOT NULL,
          path TEXT NOT NULL,
          content_sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_trial ON research_artifacts(trial_id);

        CREATE TABLE IF NOT EXISTS promotion_decisions (
          decision_id TEXT PRIMARY KEY,
          profile TEXT NOT NULL,
          verdict TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        """
    )


def register_research_governance_migration() -> None:
    if getattr(register_research_governance_migration, "_registered", False):
        return
    register_migration(
        _MIGRATION_ID, apply_research_governance, depends_on=("v2:pit_history",)
    )
    register_research_governance_migration._registered = True  # type: ignore[attr-defined]


register_research_governance_migration()
