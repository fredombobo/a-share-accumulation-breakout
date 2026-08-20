"""可信证据报告 CLI（ENTRY v1 + 净成本 IS/OOS）。

用法：
  python run_evidence_report.py
  python run_evidence_report.py --max-codes 200 --step 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from ab_screener.research.evidence import build_evidence_report, write_evidence_report


def main() -> int:
    p = argparse.ArgumentParser(description="形态基线可信证据报告")
    p.add_argument("--max-codes", type=int, default=200)
    p.add_argument("--step", type=int, default=10)
    p.add_argument(
        "--beats-baseline",
        choices=["true", "false", "unknown"],
        default="unknown",
        help="是否已验证跑赢双基线；unknown=门禁记 False",
    )
    args = p.parse_args()
    beats = None if args.beats_baseline == "unknown" else args.beats_baseline == "true"
    report = build_evidence_report(
        step=args.step,
        max_codes=args.max_codes,
        beats_baseline=beats,
    )
    paths = write_evidence_report(report)
    print(json.dumps({
        "mode": (report.get("research_plan") or {}).get("mode"),
        "can_claim_edge": report.get("can_claim_edge"),
        "promotion": report.get("promotion"),
        "is_net_pf": (report.get("is") or {}).get("net_profit_factor"),
        "oos_net_pf": (report.get("oos") or {}).get("net_profit_factor"),
        "paths": paths,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
