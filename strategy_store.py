"""策略参数注册制 + 每周擂台赛（P5 回灌层）

闭环最后一环：验证通过的参数组合注册入库，每周自动擂台赛优胜劣汰，
active 参数的样本外盈亏比作为选股排序权重回灌（scoring.py 读取）。

状态机：candidate（候选）→ active（现役）→ retired（淘汰）
- 播种：IS/OOS 验证 Top1 → active，Top2-3 → candidate
- 晋升：candidate 在最近 ARENA_EVAL_WEEKS 周切片上 PF ≥ active × ARENA_PROMOTE_MARGIN 且 DD 不劣
- 淘汰：active 连续 ARENA_DEGRADE_WEEKS 周退化（周 PF 低于自身样本外 PF 的 80%）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from config import (
    ARENA_DEGRADE_WEEKS,
    ARENA_EVAL_WEEKS,
    ARENA_PROMOTE_MARGIN,
)
from local_store import LocalStore
from optimizer import param_id
from walkforward import eval_combo


def seed_params(is_df: pd.DataFrame, oos_df: pd.DataFrame, wf_df: pd.DataFrame | None = None) -> dict:
    """首次播种：把 IS/OOS（可选 WF 复核）验证结果写入 strategy_params。

    is_df: optimizer.run_grid 的输出（含 strategy/参数列/统计列）
    oos_df: walkforward.run_is_oos 的 oos 输出
    wf_df: walkforward.wf_recheck 的输出（可选）
    返回 {"seeded": n, "active": param_id}
    """
    store = LocalStore()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if oos_df.empty:
        return {"seeded": 0, "active": None}

    wf_map = {}
    if wf_df is not None and not wf_df.empty:
        for _, r in wf_df.iterrows():
            combo = {k: r[k] for k in ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")}
            wf_map[param_id(r["strategy"], combo)] = bool(r.get("wf_pass"))

    rows = []
    best_pid, best_pf = None, -1.0
    for _, r in oos_df.iterrows():
        combo = {k: r[k] for k in ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")}
        pid = param_id(r["strategy"], combo)
        oos_pf = r.get("oos_profit_factor")
        rows.append({
            "param_id": pid,
            "strategy": r["strategy"],
            "params_json": json.dumps(combo, ensure_ascii=False, sort_keys=True),
            "status": "candidate",
            "is_profit_factor": r.get("is_profit_factor"),
            "is_win_rate": r.get("is_win_rate"),
            "is_max_dd": r.get("is_max_drawdown"),
            "oos_profit_factor": oos_pf,
            "oos_win_rate": r.get("oos_win_rate"),
            "oos_max_dd": r.get("oos_max_drawdown"),
            "wf_pass": int(wf_map[pid]) if pid in wf_map else None,
            "seeded_at": now,
        })
        if oos_pf is not None and oos_pf > best_pf:
            best_pid, best_pf = pid, oos_pf
    store.upsert_strategy_params(pd.DataFrame(rows))
    if best_pid:
        store.update_strategy_status(best_pid, "active", promoted_at=now)
    return {"seeded": len(rows), "active": best_pid}


def active_weights() -> dict[str, float]:
    """选股排序权重：{strategy: active 的样本外 PF}（无 active 时返回空，调用方默认 1.0）。"""
    df = LocalStore().load_strategy_params(status="active")
    out = {}
    for _, r in df.iterrows():
        pf = r.get("oos_profit_factor")
        if pf and pf > 0:
            out[r["strategy"]] = float(pf)
    return out


def weekly_arena(weeks: int = ARENA_EVAL_WEEKS, step: int = 10,
                 max_codes: int | None = None, dry_run: bool = False) -> dict:
    """每周擂台赛：在最近 weeks 周切片上重评 candidate 与 active，执行晋升/淘汰。

    dry_run=True 只评估不写状态。返回动作清单。
    """
    store = LocalStore()
    # 评估窗口：最近 weeks 周的日历段（用日线实际交易日切）
    dates = store.distinct_dates("daily")
    if not dates:
        return {"actions": [], "msg": "无数据"}
    eval_days = weeks * 5
    win_start = dates[-min(eval_days, len(dates))]
    win_end = dates[-1]

    params = store.load_strategy_params()
    if params.empty:
        return {"actions": [], "msg": "无注册参数"}
    active = params[params["status"] == "active"]
    cands = params[params["status"] == "candidate"]
    actions = []

    active_pf: dict[str, float] = {}
    for _, r in active.iterrows():
        combo = json.loads(r["params_json"])
        combo["strategy"] = r["strategy"]
        ev = eval_combo(combo, win_start, win_end, step=step, max_codes=max_codes)
        pf = ev.get("profit_factor")
        active_pf[r["strategy"]] = pf or 0.0
        base = r.get("oos_profit_factor") or 0.0
        degraded = bool(pf is not None and base and pf < base * 0.8)
        streak = int(r.get("degrade_streak") or 0) + 1 if degraded else 0
        actions.append({"param_id": r["param_id"], "kind": "active_eval",
                        "pf": pf, "degraded": degraded, "streak": streak})
        if not dry_run:
            if streak >= ARENA_DEGRADE_WEEKS:
                store.update_strategy_status(r["param_id"], "retired",
                                             retired_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                actions[-1]["action"] = "retired"
            else:
                store.update_strategy_status(r["param_id"], "active",
                                             weekly_oos_pf=pf, degrade_streak=streak)

    for _, r in cands.iterrows():
        combo = json.loads(r["params_json"])
        combo["strategy"] = r["strategy"]
        ev = eval_combo(combo, win_start, win_end, step=step, max_codes=max_codes)
        pf = ev.get("profit_factor")
        dd = ev.get("max_drawdown")
        ref = active_pf.get(r["strategy"], 0.0)
        ref_dd = None
        act_row = active[active["strategy"] == r["strategy"]]
        if not act_row.empty:
            ref_dd = act_row.iloc[0].get("oos_max_dd")
        promote = bool(pf and ref and pf >= ref * ARENA_PROMOTE_MARGIN
                       and (ref_dd is None or (dd or 0) <= ref_dd))
        actions.append({"param_id": r["param_id"], "kind": "candidate_eval",
                        "pf": pf, "dd": dd, "ref_active_pf": ref, "promote": promote})
        if promote and not dry_run:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            store.update_strategy_status(r["param_id"], "active", promoted_at=now, degrade_streak=0)
    return {"actions": actions, "window": (win_start, win_end)}


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--arena", action="store_true", help="跑每周擂台赛")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--weights", action="store_true", help="打印当前 active 权重")
    args = p.parse_args()
    if args.weights:
        print(active_weights())
        return 0
    if args.arena:
        print(weekly_arena(dry_run=args.dry_run))
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
