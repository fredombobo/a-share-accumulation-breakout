"""v2 迁移维护命令：--plan / --apply（只对绝对路径副本执行）。

契约（implementation P0.4）：Web 启动不自动迁移；本命令是唯一维护入口。
用法（权威环境）：
  .venv312\\Scripts\\python.exe scripts\\migrate_v2.py --db <绝对路径副本.db> --plan
  .venv312\\Scripts\\python.exe scripts\\migrate_v2.py --db <绝对路径副本.db> --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.application.pit_backfill import assert_copy_database
from ab_screener.data.migration_intents import pit_history_v2  # noqa: F401  侧效应：注册全部迁移意图
from ab_screener.data.migration_registry import apply_pending, plan_migrations


def main() -> int:
    parser = argparse.ArgumentParser(description="v2 数据库迁移维护（只对副本执行）")
    parser.add_argument("--db", required=True, help="数据库绝对路径（必须为副本，禁止生产库直改）")
    parser.add_argument("--plan", action="store_true", help="列出待应用迁移")
    parser.add_argument("--apply", action="store_true", help="应用待迁移（在副本上）")
    args = parser.parse_args()

    try:
        db = assert_copy_database(Path(args.db), maintenance_authorized=False)
    except ValueError as exc:
        print(f"错误: {exc}")
        return 2
    if not db.is_file():
        print(f"错误: 数据库不存在: {db}")
        return 2

    conn = sqlite3.connect(str(db), timeout=30)
    try:
        if args.plan:
            plan = plan_migrations(conn)
            print("待应用:", plan["pending"] or "(无)")
            print("已应用:", len(plan["already_applied"]), "| 注册总数:", plan["registered_total"])
            return 0
        if args.apply:
            applied = apply_pending(conn)
            print("已应用:", applied or "(无)")
            return 0
        parser.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
