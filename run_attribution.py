"""假突破归因 CLI（ENTRY-DEFINITION-V1）。

用法：
  python run_attribution.py
  python run_attribution.py --start 20250101 --end 20260731 --step 10 --max-codes 200
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from ab_screener.research.attribution import (
    collect_attribution_events,
    render_attribution_markdown,
    summarize_attribution,
)
from config import OUT_DIR
from local_store import LocalStore
from research_windows import recommend_research_plan

_TZ = ZoneInfo("Asia/Shanghai")


def main() -> int:
    plan = recommend_research_plan()
    p = argparse.ArgumentParser(description="A 池形态假突破归因（ENTRY v1）")
    p.add_argument("--start", default=plan.is_start)
    p.add_argument("--end", default=plan.oos_end)
    p.add_argument("--step", type=int, default=10)
    p.add_argument("--max-codes", type=int, default=250)
    args = p.parse_args()

    print(f"归因区间 {args.start}~{args.end} step={args.step} max_codes={args.max_codes}")
    print(f"研究模式 {plan.mode}（本 CLI 可用任意窗；edge 声称仍受 research_status 约束）")
    store = LocalStore()
    events = collect_attribution_events(
        store=store,
        start=args.start,
        end=args.end,
        step=args.step,
        max_codes=args.max_codes,
    )
    summary = summarize_attribution(events)
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(OUT_DIR, f"attribution_{stamp}.json")
    md_path = os.path.join(OUT_DIR, f"attribution_{stamp}.md")
    payload = {
        "summary": summary,
        "events_head": [e.to_dict() for e in events[:100]],
        "n_events": len(events),
        "params": {"start": args.start, "end": args.end, "step": args.step, "max_codes": args.max_codes},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_attribution_markdown(summary, start=args.start, end=args.end))
    print(json.dumps(summary.get("label_rates"), ensure_ascii=False, indent=2))
    print(f"n_events={len(events)}")
    print(f"写入 {json_path}")
    print(f"写入 {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
