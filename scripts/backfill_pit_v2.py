"""PIT 回填维护命令：--preflight / --run / --resume / --corporate-actions / --coverage。

契约（implementation P1.1 / V2R-D）：开始前必须满足：已验证备份、维护窗口、
目标绝对路径、预计新增空间（可用空间 ≥ 2×当前 DB 大小 + 预计新增）、WAL 预算。
只对绝对路径副本执行；禁止对生产库 --apply。
用法（权威环境）：
  .venv312\\Scripts\\python.exe scripts\\backfill_pit_v2.py --db <绝对路径副本.db> --preflight
  .venv312\\Scripts\\python.exe scripts\\backfill_pit_v2.py --db <绝对路径副本.db> \\
      --run --start 20150101 --end 20260814 --datasets daily daily_basic
  .venv312\\Scripts\\python.exe scripts\\backfill_pit_v2.py --db <绝对路径副本.db> --resume
  .venv312\\Scripts\\python.exe scripts\\backfill_pit_v2.py --db <绝对路径副本.db> \\
      --corporate-actions --resume
  .venv312\\Scripts\\python.exe scripts\\backfill_pit_v2.py --db <绝对路径副本.db> --coverage
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.application.pit_backfill import (
    ALL_DATASETS,
    CorporateActionBackfill,
    PitBackfill,
    assert_copy_database,
)
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
    parser.add_argument("--resume", action="store_true", help="从断点继续执行回填（同 --run）")
    parser.add_argument("--corporate-actions", action="store_true", help="同步公司行为（按 ts_code 分区）")
    parser.add_argument("--parity", help="生成 shadow parity 报告 JSON 输出路径（legacy vs PIT as-of）")
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
        ca = CorporateActionBackfill(db).coverage_report()
        print(f"  {ca['dataset']}: partitions={ca['partitions']} done={ca['done']}")
        return 0 if (report.get("all_done") and ca.get("all_done")) else 1

    if args.corporate_actions:
        backfill = CorporateActionBackfill(db)
        # 分区键 = stock_basic + delisted_basic 全部 ts_code
        codes = PitBackfill(db)._basic_ts_codes()
        if not codes:
            print("错误: 无标的分区键（stock_basic/delisted_basic 为空）")
            return 2
        result = backfill.run(
            codes,
            progress_cb=lambda msg, rows: print(f"  [{msg}] rows={rows}"),
        )
        print(json_dumps(result))
        return 0 if not result["failed_count"] else 1

    if args.parity:
        from ab_screener.application.data_quality import shadow_parity

        report = shadow_parity(db, seed=42)
        out = Path(args.parity)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json_dumps(report), encoding="utf-8")
        print(json_dumps(report))
        print(f"parity 报告已写入: {out}")
        return 0 if report["result"] == "PASS" else 1

    if args.run or args.resume:
        import sqlite3 as _sqlite3

        # --resume 未给窗口时从已有 checkpoints 推导（断点续跑语义，离线）
        if not args.start or not args.end:
            if not args.resume:
                print("错误: --run 需要 --start 与 --end（daily 族分区推导用）")
                return 2
            with _sqlite3.connect(str(db)) as _c:
                rows = _c.execute(
                    "SELECT MIN(partition_key), MAX(partition_key) FROM pit_backfill_checkpoints"
                    " WHERE dataset IN ('daily','daily_basic','moneyflow','adj_factor')"
                ).fetchone()
            if not rows or not rows[0] or not rows[1]:
                print("错误: 无既有 checkpoint 可推导 --resume 窗口（需先 --run --start/--end）")
                return 2
            args.start, args.end = str(rows[0]), str(rows[1])
            print(f"[resume] 从 checkpoints 推导窗口: {args.start} ~ {args.end}")
        backfill = PitBackfill(db)
        # resume 时用 checkpoints 既有分区键做离线计划，避免重算交易日历（无 Token 环境）
        partitions = None
        if args.resume:
            partitions = backfill.checkpoint_partitions(args.datasets)
            # 只续跑已有 checkpoint 的数据集；无 checkpoint 的数据集跳过（不触发网络）
            resume_datasets = [ds for ds in args.datasets if ds in partitions]
            skipped_ds = [ds for ds in args.datasets if ds not in partitions]
            if skipped_ds:
                print(f"[resume] 跳过无 checkpoint 数据集: {sorted(skipped_ds)}")
            if not resume_datasets:
                print("[resume] 无既有 checkpoint 可续跑")
                return 0
            args.datasets = resume_datasets
        result = backfill.run(
            args.datasets, start=args.start, end=args.end, partitions=partitions,
            workers=max(1, args.workers),
            progress_cb=lambda msg, rows: print(f"  [{msg}] rows={rows}"),
        )
        print(json_dumps(result))
        return 0 if not result["failed"] else 1

    parser.print_help()
    return 0


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
