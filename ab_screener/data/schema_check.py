"""schema 兼容性检查（Web 启动只断言，不自动 DDL）。

契约（implementation P0.4）：Web 启动运行 assert_schema_compatible()；
不得自动执行大表 DDL/backfill，也不得吞掉 required migration 异常。
维护命令固定为 .venv312\\Scripts\\python.exe scripts\\migrate_v2.py --db <副本> --plan|--apply。
"""
from __future__ import annotations

from pathlib import Path

from ab_screener.data.migration_registry import schema_compatible


def assert_schema_compatible(db_path: str | Path) -> None:
    """不兼容即抛 RuntimeError（fail-closed），绝不在启动时自动迁移。"""
    ok, issues = schema_compatible(db_path)
    if not ok:
        raise RuntimeError(
            "数据库 schema 与代码不兼容，拒绝启动。"
            f"请先对副本执行 migrate_v2.py: {issues}"
        )
