"""legacy 回测工作台路由（G2 拆路由第 4 步）。
共享状态从 ab_screener.api.legacy_state import；领域模块（paper_trading / research /
scan_spawn 等）函数内延迟 import，保持与原实现一致。
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, HTTPException

from ab_screener.api.legacy_state import (
    _BT_LOCK,
    _BT_TASKS,
    _BT_TASKS_MAX,
)

router = APIRouter(tags=["legacy"])

# ═══════════════════════════════════════════════════════════
# 回测工作台 API（2026-08-16 新增：单组参数 → IS/OOS 逐笔明细）
# ═══════════════════════════════════════════════════════════


def _bt_prune() -> None:
    if len(_BT_TASKS) > _BT_TASKS_MAX:
        for key in list(_BT_TASKS)[:-_BT_TASKS_MAX]:
            _BT_TASKS.pop(key, None)


@router.post("/api/backtest/run")
def backtest_run(body: dict):
    """启动一次工作台回测（后台执行）。请求体见前端 BacktestStudio。"""
    from ab_screener.research.backtest_engine import run_single_backtest
    from optimizer import ResearchCancelled
    from research_windows import recommend_research_plan
    from walkforward import wf_recheck

    strategy = str(body.get("strategy") or "A")
    exit_p = {
        key: body[key]
        for key in ("vol_ratio_min", "stop_pct", "exit_window", "strong_reset")
        if body.get(key) is not None
    }
    signal_kwargs = {k: v for k, v in (body.get("signal") or {}).items() if v is not None}
    costs = body.get("costs") or None
    max_codes = max(20, min(int(body.get("max_codes") or 600), 4500))
    step = max(1, min(int(body.get("step") or 10), 60))
    include_wf = bool(body.get("include_wf", True))
    include_baselines = bool(body.get("include_baselines", True))

    # 窗口：auto 用研究窗推荐；manual 用显式 IS/OOS
    win = body.get("windows") or {}
    if str(win.get("mode") or "auto") == "manual":
        is_start = str(win.get("is_start") or "")
        is_end = str(win.get("is_end") or "")
        oos_start = str(win.get("oos_start") or "")
        oos_end = str(win.get("oos_end") or "")
        if not (len(is_start) == 8 and len(is_end) == 8 and len(oos_start) == 8 and len(oos_end) == 8):
            raise HTTPException(status_code=422, detail="手动窗口需提供 is_start/is_end/oos_start/oos_end（YYYYMMDD）")
        mode_label = "manual"
        wf_windows = []
    else:
        plan = recommend_research_plan()
        if plan.mode == "insufficient":
            raise HTTPException(status_code=400, detail="日线覆盖不足，无法回测")
        is_start, is_end = plan.is_start, plan.is_end
        oos_start, oos_end = plan.oos_start, plan.oos_end
        mode_label = plan.mode
        wf_windows = plan.wf_windows or []

    task_id = uuid.uuid4().hex[:12]
    with _BT_LOCK:
        _bt_prune()
        _BT_TASKS[task_id] = {
            "status": "running",
            "stage": "准备回测…",
            "progress": 0,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "cancel_requested": False,
            "result": None,
            "error": None,
        }

    def _cancel_flag() -> bool:
        with _BT_LOCK:
            return bool(_BT_TASKS.get(task_id, {}).get("cancel_requested"))

    def _progress(msg: str, pct: int) -> None:
        with _BT_LOCK:
            task = _BT_TASKS.get(task_id)
            if task:
                task["stage"] = msg
                task["progress"] = max(0, min(100, int(pct)))

    def _run() -> None:
        from tushare_init import sanitize_error

        try:
            _progress("IS 样本内回放…", 3)
            is_r = run_single_backtest(
                strategy=strategy, exit_params=exit_p, signal_kwargs=signal_kwargs,
                costs=costs, start=is_start, end=is_end, step=step, max_codes=max_codes,
                progress_cb=lambda m, p: _progress(f"IS · {m}", 3 + int(34 * p / 100)),
                cancel_check=_cancel_flag,
            )
            if is_r.get("error"):
                raise RuntimeError(is_r["error"])
            _progress("OOS 样本外回放…", 40)
            oos_r = run_single_backtest(
                strategy=strategy, exit_params=exit_p, signal_kwargs=signal_kwargs,
                costs=costs, start=oos_start, end=oos_end, step=step, max_codes=max_codes,
                progress_cb=lambda m, p: _progress(f"OOS · {m}", 40 + int(30 * p / 100)),
                cancel_check=_cancel_flag,
            )
            if oos_r.get("error"):
                raise RuntimeError(oos_r["error"])

            wf: dict | None = None
            if include_wf and wf_windows:
                _progress("Walk-forward 三窗复核…", 72)
                combo = {"strategy": strategy, 
                    "vol_ratio_min": exit_p.get("vol_ratio_min", 1.5),
                    "stop_pct": exit_p.get("stop_pct", 0.07),
                    "exit_window": exit_p.get("exit_window", 10),
                    "strong_reset": exit_p.get("strong_reset", 3)
                }
                wf_df = wf_recheck(
                    [combo], step=step, max_codes=max_codes, windows=wf_windows,
                    progress_cb=lambda m, p: _progress(f"WF · {m}", 72 + int(18 * p / 100)),
                    signal_kwargs=signal_kwargs, costs=costs,
                    cancel_check=_cancel_flag,
                )
                if not wf_df.empty:
                    wf = wf_df.iloc[0].to_dict()
                    wf = {k: v.item() if hasattr(v, "item") else v for k, v in wf.items()}
                _progress("Walk-forward 完成", 90)

            baselines: dict | None = None
            if include_baselines:
                from ab_screener.research.baselines import ma_cross_baseline, random_baseline_trades
                from local_store import LocalStore

                oos_metrics = oos_r.get("metrics") or {}
                requested = max(20, int(oos_metrics.get("net_n_trades") or 40))
                hold_days = int(exit_p.get("exit_window") or 10)
                load_start = (pd.to_datetime(oos_start) - pd.Timedelta(days=365)).strftime("%Y%m%d")
                daily = LocalStore().load_daily(
                    ts_codes=None, start=load_start, end=oos_end
                )
                from optimizer import research_universe

                universe = research_universe(max_codes, include_delisted=True)
                baselines = {
                    "random": random_baseline_trades(
                        daily, n_trades=requested, hold_days=hold_days,
                        entry_start=oos_start, entry_end=oos_end, codes=universe,
                    ),
                    "ma20_60": ma_cross_baseline(
                        daily, hold_days=hold_days, max_trades=requested,
                        entry_start=oos_start, entry_end=oos_end, codes=universe,
                    ),
                }
                _progress("基线对比完成", 94)

            is_metrics = is_r.get("metrics") or {}
            oos_metrics = oos_r.get("metrics") or {}
            is_pf = is_metrics.get("net_profit_factor")
            oos_pf = oos_metrics.get("net_profit_factor")
            result = {
                "task_id": task_id,
                "params": {
                    "strategy": strategy,
                    "exit": exit_p,
                    "signal": signal_kwargs or None,
                    "costs": costs,
                    "max_codes": max_codes,
                    "step": step,
                },
                "windows": {
                    "mode": mode_label,
                    "is": [is_start, is_end],
                    "oos": [oos_start, oos_end],
                },
                "is": is_r,
                "oos": oos_r,
                "hold_ratio": {
                    "pf": round(oos_pf / is_pf, 3) if is_pf and oos_pf is not None else None,
                },
                "wf": wf,
                "baselines": baselines,
                "disclaimer": "研究辅助，不是投资建议；宇宙包含上市+退市全历史（已消除幸存者偏差），历史回测结果更保守可信。",
            }
            with _BT_LOCK:
                task = _BT_TASKS.get(task_id, {})
                task.update(status="done", progress=100, stage="回测完成", result=result)
        except ResearchCancelled:
            with _BT_LOCK:
                _BT_TASKS[task_id].update(status="cancelled", stage="已取消")
        except Exception as exc:  # noqa: BLE001
            with _BT_LOCK:
                _BT_TASKS[task_id].update(
                    status="error", stage="回测失败", error=sanitize_error(exc)[:300],
                )

    threading.Thread(target=_run, daemon=True, name=f"bt-{task_id[:6]}").start()
    return {"task_id": task_id}


@router.get("/api/backtest/status/{task_id}")
def backtest_status(task_id: str):
    with _BT_LOCK:
        task = _BT_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return dict(task)


@router.post("/api/backtest/{task_id}/cancel")
def backtest_cancel(task_id: str):
    with _BT_LOCK:
        task = _BT_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        task["cancel_requested"] = True
        if task.get("status") == "running":
            task["stage"] = "取消中…"
        return {"ok": True}


