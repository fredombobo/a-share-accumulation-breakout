"""迁移意图：AI 解读缓存表 `ai_insights`（P8.1，旁路功能）。

- `ai_insights`：A 池候选的 DeepSeek 五维评分解读缓存，按 (ts_code, signal_date) 幂等。
- 非核心业务表：可安全重建；由 `intelligence.ai_analysis` 惰性建表兜底，
  本迁移意图仅供 `scripts/migrate_v2.py --apply` 显式预建。
- 依赖 `v2:signals`（解读对象是 A 池候选信号）。
"""
from __future__ import annotations

import sqlite3

from ab_screener.data.migration_registry import register_migration

_MIGRATION_ID = "v2:ai_insight"


def apply_ai_insight(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_insights (
          ts_code TEXT NOT NULL,
          signal_date TEXT NOT NULL,
          run_id TEXT NOT NULL,
          provider TEXT NOT NULL,
          prompt_hash TEXT NOT NULL,
          ai_text TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (ts_code, signal_date)
        );
        CREATE INDEX IF NOT EXISTS idx_ai_insights_created ON ai_insights(created_at);
        """
    )


def register_ai_insight_migrations() -> None:
    if getattr(register_ai_insight_migrations, "_registered", False):
        return
    register_migration(_MIGRATION_ID, apply_ai_insight, depends_on=("v2:signals",))
    register_ai_insight_migrations._registered = True  # type: ignore[attr-defined]


register_ai_insight_migrations()
