"""迁移意图：Review 与决策台账（P7.4）。

- `research_notes`：研究假设/想法/人工决策日志（append-only 参考 run/signal/order/experiment）。
- `review_decisions`：人工决策台账（action + rationale + risk flags）。

依赖 `v2:research_governance`（引用 experiment/run IDs 语义一致）。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:review"


def apply_review(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS research_notes (
          note_id TEXT PRIMARY KEY,
          ref_type TEXT NOT NULL CHECK (ref_type IN
            ('experiment','run','signal','order','candidate','shadow','retirement','none')),
          ref_id TEXT,
          kind TEXT NOT NULL CHECK (kind IN ('idea','hypothesis','decision','log','weekly')),
          title TEXT NOT NULL,
          body TEXT NOT NULL DEFAULT '',
          tags_json TEXT NOT NULL DEFAULT '[]',
          created_by TEXT NOT NULL DEFAULT 'user',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notes_ref ON research_notes(ref_type, ref_id);
        CREATE INDEX IF NOT EXISTS idx_notes_created ON research_notes(created_at);

        CREATE TABLE IF NOT EXISTS review_decisions (
          decision_id TEXT PRIMARY KEY,
          ref_type TEXT NOT NULL,
          ref_id TEXT,
          action TEXT NOT NULL,
          rationale TEXT NOT NULL,
          risk_flags_json TEXT NOT NULL DEFAULT '[]',
          created_by TEXT NOT NULL DEFAULT 'user',
          decided_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_ref ON review_decisions(ref_type, ref_id);
        CREATE INDEX IF NOT EXISTS idx_decisions_at ON review_decisions(decided_at);
        """
    )


def register_review_migrations() -> None:
    if getattr(register_review_migrations, "_registered", False):
        return
    register_migration(
        _MIGRATION_ID, apply_review, depends_on=("v2:research_governance",)
    )
    register_review_migrations._registered = True  # type: ignore[attr-defined]


register_review_migrations()
