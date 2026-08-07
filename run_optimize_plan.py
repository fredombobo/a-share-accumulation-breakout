"""运行方案 X 的 IS 网格 + OOS 验证（脚本文件方式——Windows spawn 需要正常模块入口）。

窗口默认取 research_windows.recommend_research_plan()（数据不足时自动降级）。

用法:
  python run_optimize_plan.py A
  python run_optimize_plan.py A 600 10
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from research_windows import recommend_research_plan  # noqa: E402
from walkforward import run_is_oos  # noqa: E402


def main() -> int:
    strategy = sys.argv[1] if len(sys.argv) > 1 else "A"
    max_codes = int(sys.argv[2]) if len(sys.argv) > 2 else 4500
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    plan = recommend_research_plan()
    if plan.mode == "insufficient":
        print(f"[run_optimize_plan] ABORT mode=insufficient n_dates={plan.n_dates}", flush=True)
        for n in plan.notes:
            print(f"  · {n}", flush=True)
        return 2
    print(
        f"[run_optimize_plan] strategy={strategy} max_codes={max_codes} step={step} "
        f"mode={plan.mode} IS={plan.is_start}~{plan.is_end} OOS={plan.oos_start}~{plan.oos_end}",
        flush=True,
    )
    if not plan.can_claim_edge:
        print("[run_optimize_plan] WARN: degraded window — 结果仅供摸底，不可当 edge", flush=True)
    r = run_is_oos(
        strategy=strategy,
        step=step,
        max_codes=max_codes,
        top_n=3,
        is_start=plan.is_start,
        is_end=plan.is_end,
        oos_start=plan.oos_start,
        oos_end=plan.oos_end,
        progress_cb=lambda m, pct: print(f"[{pct:3d}%] {m}", flush=True),
    )
    out = {
        "is_top": r["is"].head(8).to_dict("records") if not r["is"].empty else [],
        "oos": r["oos"].to_dict("records") if not r["oos"].empty else [],
        "msg": r.get("msg"),
        "research_mode": plan.mode,
        "windows": {
            "is_start": plan.is_start,
            "is_end": plan.is_end,
            "oos_start": plan.oos_start,
            "oos_end": plan.oos_end,
            "can_claim_edge": plan.can_claim_edge,
        },
    }
    os.makedirs("runtime", exist_ok=True)
    path = os.path.join("runtime", f"is_oos_{strategy}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"DONE_{strategy} saved {path} mode={plan.mode}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
