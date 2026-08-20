"""真实可信研究运行入口（阶段 2）：full 窗网格 + 正式统计块 + 双基线。

用法（权威环境）：
  .venv312\\Scripts\\python.exe scripts\\run_trusted_research_real.py
    [--max-codes 400] [--step 10] [--strategy A] [--out runtime/v2/research]

产出：runtime/v2/research/trusted_report_<run_id>.json（含 v2_statistics 正式统计块）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ab_screener.research.trusted_run import execute_trusted_research  # noqa: E402
from research_windows import recommend_research_plan  # noqa: E402

_TZ = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser(description="真实可信研究（full 窗 + 正式统计）")
    parser.add_argument("--max-codes", type=int, default=400)
    parser.add_argument("--step", type=int, default=10)
    parser.add_argument("--strategy", default="A")
    parser.add_argument("--db", default="runtime/stock_data.db")
    parser.add_argument("--out", default="runtime/v2/research")
    args = parser.parse_args()

    from build_version import build_version

    plan = recommend_research_plan()
    plan_d = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
    windows = {
        "is_start": plan_d["is_start"],
        "is_end": plan_d["is_end"],
        "oos_start": plan_d["oos_start"],
        "oos_end": plan_d["oos_end"],
        "mode": plan_d["mode"],
        "automatic_window": True,  # recommend_research_plan 即自动窗
    }
    print(f"[research] mode={windows['mode']} IS={windows['is_start']}~{windows['is_end']}"
          f" OOS={windows['oos_start']}~{windows['oos_end']}")

    run_id = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = execute_trusted_research(
        research_run_id=run_id,
        request={
            "strategy": args.strategy,
            "mode": "grid",
            "max_codes": args.max_codes,
            "step": args.step,
        },
        windows=windows,
        db_path=args.db,
        code_version=build_version(),
        dataset_version=plan_d.get("dataset_version") or "auto",
        phase_cb=lambda phase, pct, message, state: (
            print(f"[{phase} {pct:>3}%] {message}") if pct % 20 == 0 or phase == "REPORT" else None
        ),
    )
    report = result.get("report") or {}
    path = out_dir / f"trusted_report_{run_id}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[research] report saved: {path}")
    gate = report.get("gate") or {}
    print(f"[research] gate: {gate.get('verdict')} reasons={gate.get('reasons')}")
    stats = report.get("v2_statistics") or {}
    print(f"[research] v2_statistics: status={stats.get('status')}"
          + (f" dsr={stats.get('dsr')} mintrl={stats.get('min_track_record_length')}"
             if stats.get("status") == "OK" else f" reason={stats.get('reason')}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
