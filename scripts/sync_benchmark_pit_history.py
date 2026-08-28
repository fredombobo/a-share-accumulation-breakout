"""Safely repair CSI300 PIT history using the configured HTTPS provider."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.data.benchmark_pit_sync import sync_benchmark_pit_history


def main() -> int:
    parser = argparse.ArgumentParser(description="补齐权威研究所需的 CSI300 PIT 历史")
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--benchmark", default="000300.SH")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()

    provider = None
    if args.apply:
        from tushare_init import get_pro

        provider = get_pro()
    result = sync_benchmark_pit_history(
        Path(args.db).resolve(),
        provider,
        benchmark_code=args.benchmark,
        start=args.start,
        end=args.end,
        apply=args.apply,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        report_path = Path(args.report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] in {"NOOP", "PLANNED", "COMPLETED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
