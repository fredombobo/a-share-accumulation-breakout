"""PIT 回填维护命令：--preflight / --run / --coverage（只对绝对路径副本执行）。

契约（implementation P1.1）：开始前必须满足：已验证备份、维护窗口、目标绝对路径、
预计新增空间（可用空间 ≥ 2×当前 DB 大小 + 预计新增）、WAL 预算。
用法（权威环境）：
  .venv312\\Scripts\\python.exe scripts\\backfill_pit_v2.py --db <绝对路径副本.db> --preflight
  .venv312\\Scripts\\python.exe scripts\\backfill_pit_v2.py --db <绝对路径副本.db> \\
      --run --start 20150101 --end 20260814 --datasets daily daily_basic
  .venv312\\Scripts\\python.exe scripts\\backfill_pit_v2.py --db <绝对路径副本.db> --coverage
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.application.pit_backfill import ALL_DATASETS, PitBackfill, assert_copy_database
from ab_screener.data.migration_registry import pending_migrations

DEFAULT_DATASETS = list(ALL_DATASETS)


def _free_space_bytes(path: Path) -> int:
    usage = shutil.disk_usage(path.anchor or path.parent)
    return usage.free


def main() -> int:
    parser = argparse.ArgumentParser(description="PIT 历史回填（只对副本执行）")
    parser.add_argument("--db", required=True, help="数据库绝对路径（必须为副本）")
    parser.add_argument("--preflight", action="store_true", help="检查迁移就绪条件")
    parser.add_argument("--run", action="store_true", help="执行回填（断点续跑）")
    parser.add_argument("--coverage", action="store_true", help="打印覆盖率报告")
    parser.add_argument("--start", help="回填起始日 YYYYMMDD（daily 族必需）")
    parser.add_argument("--end", help="回填结束日 YYYYMMDD（daily 族必需）")
    parser.add_argument("--datasets", nargs="*", default=DEFAULT_DATASETS,
                        help="数据集列表，默认全部")
    parser.add_argument("--workers", type=int, default=1,
                        help="拉取并发数（网络并发，写库保持串行；建议 4-8）")
    args = parser.parse_args()

    db = Path(args.db)
    try:
        db = assert_copy_database(db, maintenance_authorized=False)
    except ValueError as exc:
        print(f"错误: {exc}")
        return 2
    if db.name == "stock_data.db" and "runtime" in db.parts:
        print("错误: 拒绝操作生产库 runtime/stock_data.db")
        return 2
    if not db.is_file():
        print(f"错误: 数据库不存在: {db}")
        return 2

    import sqlite3

    conn = sqlite3.connect(str(db), timeout=30)
    try:
        pending = pending_migrations(conn)
    finally:
        conn.close()
    if "v2:pit_history" in pending:
        print("错误: 尚未应用迁移 v2:pit_history（先运行 scripts/migrate_v2.py --apply）")
        return 2

    if args.preflight:
        size = db.stat().st_size
        free = _free_space_bytes(db)
        need = 2 * size + size  # 2×DB + 预计新增（保守：至少与 DB 等量新增）
        print(f"DB 大小: {size / 1e6:.1f} MB")
        print(f"可用空间: {free / 1e6:.1f} MB")
        print(f"需要 ≥ {need / 1e6:.1f} MB（2×当前 + 预计新增）")
        print("迁移已注册: v2:pit_history 已应用" if "v2:pit_history" not in pending
              else "迁移未应用")
        print("要求: 已验证备份 / 维护窗口 / 目标为副本绝对路径 / WAL 预算")
        if free < need:
            print("结果: FAIL（可用空间不足）")
            return 1
        print("结果: PASS（可进入维护窗口回填）")
        return 0

    if args.coverage:
        report = PitBackfill(db).coverage_report(args.datasets)
        for ds, v in report.items():
            if ds != "all_done":
                print(f"  {ds}: partitions={v['partitions']} done={v['done']} rows={v['rows']}")
        print("all_done:", report.get("all_done"))
        return 0 if report.get("all_done") else 1

    if args.run:
        if not args.start or not args.end:
            print("错误: --run 需要 --start 与 --end（daily 族分区推导用）")
            return 2
        backfill = PitBackfill(db)
        result = backfill.run(
            args.datasets, start=args.start, end=args.end, workers=max(1, args.workers),
            progress_cb=lambda msg, rows: print(f"  [{msg}] rows={rows}"),
        )
        print(json_dumps(result))
        return 0

    parser.print_help()
    return 0


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
