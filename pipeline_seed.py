"""P5 播种脚本：IS/OOS 结果 → WF 复核 → seed_params → 擂台赛干跑

用法（在 A/B 优化完成后）：
  python -m pipeline_seed A
  python -m pipeline_seed B
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from research_windows import recommend_research_plan
from strategy_store import seed_params, weekly_arena
from walkforward import wf_recheck


def seed_from_result(strategy: str, max_codes: int = 2000, step: int = 5) -> dict:
    path = os.path.join("runtime", f"is_oos_{strategy}.json")
    if not os.path.exists(path):
        return {"error": f"{path} 不存在，先跑优化"}
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    is_df = pd.DataFrame(data.get("is_top") or [])
    oos_df = pd.DataFrame(data.get("oos") or [])
    if oos_df.empty:
        return {"error": "OOS 为空", "msg": data.get("msg")}

    plan = recommend_research_plan()
    wf_windows = plan.wf_windows or None
    if plan.mode != "full":
        print(f"[pipeline_seed] research mode={plan.mode} — 使用数据驱动 WF 窗，不可声称 edge")
    # WF 复核（窗口来自 research_windows；full 时贴近标准 WF）
    combos = []
    for _, r in oos_df.iterrows():
        combos.append({k: r[k] for k in ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")})
    wf_df = wf_recheck(combos, step=step, max_codes=max_codes,
                       progress_cb=lambda m, pct: print(f"[WF {pct:3d}%] {m}"), windows=wf_windows)
    print("=== WF 复核 ===")
    print(wf_df.to_string())

    seeded = seed_params(is_df, oos_df, wf_df)
    print("=== 播种 ===", seeded)
    arena = weekly_arena(dry_run=True, max_codes=max_codes, step=step)
    print("=== 擂台赛干跑 ===")
    for a in arena["actions"]:
        print(" ", a)
    return {"seeded": seeded, "arena": arena}


if __name__ == "__main__":
    strat = sys.argv[1] if len(sys.argv) > 1 else "A"
    max_codes = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    out = seed_from_result(strat, max_codes=max_codes, step=step)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:800])
