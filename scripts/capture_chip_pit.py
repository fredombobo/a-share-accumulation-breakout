"""每日筹码 PIT 前向捕获：收盘后抓当日 cyq_perf，按真实抓取时刻写入 cyq_history。

用法（权威环境）：
  .venv312\\Scripts\\python.exe scripts\\capture_chip_pit.py              # 只出计划，不访问网络
  .venv312\\Scripts\\python.exe scripts\\capture_chip_pit.py --apply      # 抓取并追加写入
  .venv312\\Scripts\\python.exe scripts\\capture_chip_pit.py --readiness  # 只读：严格 PIT 可用交易日统计

退出码：0 = PLANNED/COMPLETED/NOOP；3 = PARTIAL/EMPTY（覆盖不足或供应商尚未发布）；2 = 拒绝执行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.data.chip_pit_capture import (
    MIN_COVERAGE,
    ChipCaptureError,
    capture_chip_snapshot,
    chip_pit_readiness,
)


def _emit(payload: dict, report: str | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if report:
        path = Path(report).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> int:
    parser = argparse.ArgumentParser(description="筹码分布前向 PIT 捕获（只前向，不补历史）")
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--trade-date", help="目标交易日 YYYYMMDD（默认 daily 最新交易日）")
    parser.add_argument("--apply", action="store_true", help="访问数据源并追加写入")
    parser.add_argument("--min-coverage", type=float, default=MIN_COVERAGE)
    parser.add_argument("--readiness", action="store_true", help="只读输出严格 PIT 可用交易日统计")
    parser.add_argument("--since", help="--readiness 统计起点 YYYYMMDD")
    parser.add_argument("--report", help="结果 JSON 另存路径")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    try:
        if args.readiness:
            summary = chip_pit_readiness(db, since=args.since)
            summary["usable"] = summary["usable"][-10:]  # 终端只显示最近 10 个
            _emit(summary, args.report)
            return 0
        provider = None
        if args.apply:
            from tushare_init import get_pro, sanitize_error

            provider = get_pro()
        try:
            result = capture_chip_snapshot(
                db,
                provider,
                trade_date=args.trade_date,
                apply=args.apply,
                min_coverage=args.min_coverage,
            )
        except ChipCaptureError:
            raise
        except Exception as exc:  # noqa: BLE001 - 供应商异常统一脱敏后失败
            if args.apply:
                raise ChipCaptureError(sanitize_error(exc)) from exc
            raise
    except ChipCaptureError as exc:
        _emit({"status": "REFUSED", "error": str(exc)}, args.report)
        return 2
    if result["status"] != "PLANNED":
        readiness = chip_pit_readiness(db)
        result["strict_usable_dates_total"] = readiness["strict_usable_dates"]
        result["first_strict_usable"] = readiness["first_usable"]
    _emit(result, args.report)
    return 0 if result["status"] in {"PLANNED", "COMPLETED", "NOOP"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
