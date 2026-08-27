"""legacy 策略实验室路由（G2 拆路由第 4 步）。

迁自 web/backend_app.py 的实验室域：_recover_orphaned_lab_runs / LabOptimizeRequest /
_lab_running / _select_lab_task / _lab_public_record / _run_lab_worker /
_resolve_lab_windows / _report_payload，及 lab/research-status / catalog / optimize /
status / reports / cancel / leaderboard / compare / arena 路由。
共享状态（Lab 任务/锁/store）从 ab_screener.api.legacy_state import。
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ab_screener.api.legacy_state import (
    _BUILD_VERSION,
    _LAB_LOCK,
    _LAB_STORE,
    _LAB_TASKS,
    _LAB_TASKS_MAX,
    _LOGGER,
    _store,
)

router = APIRouter(tags=["legacy"])


# ── 策略实验室（P6：闭环优化 → 验证 → 擂台赛） ──
def _recover_orphaned_lab_runs(process_name: str | None = None) -> int:
    """Recover only in the web process, never in spawned optimizer workers.

    On Windows a ProcessPool worker imports this module again.  Running recovery
    there would quarantine the parent web process's active Lab row and make the
    parent mistake that interruption for a user cancellation.
    """
    if process_name is None:
        from multiprocessing import current_process

        process_name = current_process().name
    if process_name != "MainProcess":
        return 0
    return _LAB_STORE.mark_orphaned_interrupted()


_recover_orphaned_lab_runs()


class LabOptimizeRequest(BaseModel):
    strategy: str = "A"  # A: 形态入场+标杆量出场 | B: 五步抓主升+标杆量出场
    # 空字符串 = 使用 research_windows 自动窗；也可手填 YYYYMMDD
    is_start: str = ""
    is_end: str = ""
    oos_start: str = ""
    oos_end: str = ""
    max_codes: int = 4500
    step: int = 5
    # grid=网格搜索 | single=单组人工试跑
    mode: str = "grid"
    # 自定义网格（键 → 取值列表）；空则用 config.GRID_BENCH
    grid: dict | None = None
    # 单组参数（mode=single 时必填）
    vol_ratio_min: float | None = None
    strong_reset: int | None = None
    exit_window: int | None = None
    stop_pct: float | None = None
    force: bool = False


def _lab_running() -> str | None:
    active = _LAB_STORE.latest_active()
    return str(active["research_run_id"]) if active is not None else None


def _select_lab_task(tasks: dict[str, dict]) -> tuple[str, dict] | None:
    """Select the task a returning Lab page should restore.

    An active task always wins over a newer terminal task.  This keeps a route
    remount from hiding work that is still in progress.
    """
    if not tasks:
        return None
    active_states = {"pending", "running", "cancelling"}
    active = [(task_id, task) for task_id, task in tasks.items() if task.get("status") in active_states]
    candidates = active or list(tasks.items())
    return max(candidates, key=lambda item: item[1].get("started_at") or "")


def _lab_public_record(record: dict) -> dict:
    raw_request = record.get("request")
    request_data: dict = raw_request if isinstance(raw_request, dict) else {}
    return {
        "task_id": record.get("research_run_id"),
        "research_run_id": record.get("research_run_id"),
        "status": record.get("status") or "idle",
        "phase": record.get("phase"),
        "progress": int(record.get("progress") or 0),
        "message": record.get("message"),
        "error": record.get("message") if record.get("status") == "error" else None,
        "result": record.get("result"),
        "strategy": record.get("strategy"),
        "windows": request_data.get("_windows"),
        "verdict": record.get("verdict"),
        "candidate_eligible": bool(record.get("candidate_eligible")),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
    }


def _resolve_lab_windows(req: LabOptimizeRequest) -> dict:
    """解析 Lab 窗口：缺省走数据驱动 plan。"""
    from research_windows import recommend_research_plan

    plan = recommend_research_plan()
    use_auto = not (req.is_start and req.is_end and req.oos_start and req.oos_end)
    if use_auto:
        return {
            "is_start": plan.is_start,
            "is_end": plan.is_end,
            "oos_start": plan.oos_start,
            "oos_end": plan.oos_end,
            "mode": plan.mode,
            "can_claim_edge": plan.can_claim_edge,
            "data_ready_for_edge_validation": plan.data_ready_for_edge_validation,
            "label": plan.label,
            "notes": plan.notes,
            "n_dates": plan.n_dates,
            "wf_windows": plan.to_dict().get("wf_windows", []),
            "automatic_window": True,
        }
    return {
        "is_start": req.is_start,
        "is_end": req.is_end,
        "oos_start": req.oos_start,
        "oos_end": req.oos_end,
        "mode": "manual",
        "can_claim_edge": False,
        "label": "手动窗口",
        "notes": ["手动指定窗口，请自行确认无未来函数与覆盖充足"],
        "n_dates": plan.n_dates,
        "wf_windows": [],
        "automatic_window": False,
    }


def _run_lab_worker(task_id: str, req: LabOptimizeRequest, windows: dict) -> None:
    from ab_screener.research.trusted_run import execute_trusted_research
    from optimizer import ResearchCancelled

    stored = _LAB_STORE.get(task_id) or {}
    request_data = dict(stored.get("request") or {})
    request_data.pop("_windows", None)
    request_data.pop("force", None)
    checkpoint = stored.get("checkpoint") or {}

    def phase(phase_name: str, pct: int, message: str, state: dict) -> None:
        if _LAB_STORE.is_cancel_requested(task_id):
            raise ResearchCancelled("用户取消")
        task = _LAB_TASKS.get(task_id)
        if task is None:
            raise RuntimeError("任务状态丢失")
        task.update({"status": "running", "phase": phase_name, "progress": pct, "message": message})
        _LAB_STORE.update(
            task_id,
            status="running",
            phase=phase_name,
            progress=pct,
            message=message,
            checkpoint=state,
        )

    try:
        task = _LAB_TASKS.get(task_id)
        if task is None:
            return
        task.update({"status": "running", "windows": windows})
        _LAB_STORE.update(
            task_id,
            status="running",
            phase=stored.get("phase") or "IS",
            progress=int(stored.get("progress") or 0),
        )
        result = execute_trusted_research(
            research_run_id=task_id,
            request=request_data,
            windows=windows,
            db_path=_store.db_path,
            code_version=str(stored.get("code_version") or _BUILD_VERSION),
            dataset_version=str(stored.get("dataset_version") or "unknown"),
            phase_cb=phase,
            checkpoint=checkpoint,
            cancel_check=lambda: _LAB_STORE.is_cancel_requested(task_id),
        )
        state = result.pop("checkpoint")
        report = result.get("trusted_report") or {}
        frozen = result.get("frozen_candidate") or {}
        primary = frozen.get("is") or {}
        if report.get("candidate_eligible") and primary.get("param_id"):
            _LAB_STORE.add_candidate(
                task_id,
                strategy=str(primary.get("strategy") or req.strategy),
                param_id=str(primary["param_id"]),
                params={
                    key: primary.get(key)
                    for key in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
                },
                metrics={
                    **(frozen.get("oos") or {}),
                    "anti_overfit_version": (report.get("anti_overfit") or {}).get("version"),
                    "gate_verdict": report.get("verdict"),
                    "report_sha256": hashlib.sha256(
                        str(report.get("markdown") or "").encode("utf-8")
                    ).hexdigest(),
                },
            )
        task.update(
            {
                "status": "done",
                "phase": "CANDIDATE",
                "progress": 100,
                "message": report.get("summary") or "可信报告已生成",
                "result": result,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        _LAB_STORE.update(
            task_id,
            status="done",
            phase="CANDIDATE",
            progress=100,
            message=str(task["message"]),
            checkpoint=state,
            result=result,
            is_rows=result.get("is_all") or [],
            oos_rows=result.get("oos") or [],
            baselines=result.get("baselines") or {},
            promotion=result.get("promotion_checks") or {},
            verdict=report.get("verdict"),
            candidate_eligible=bool(report.get("candidate_eligible")),
            can_claim_edge=bool(report.get("candidate_eligible")),
            report_markdown=report.get("markdown") or "",
        )
    except Exception as exc:  # noqa: BLE001 worker boundary must persist terminal state
        task = _LAB_TASKS.get(task_id)
        persisted_before = _LAB_STORE.get(task_id)
        persisted_cancel = bool(
            persisted_before
            and (persisted_before.get("cancel_requested") or persisted_before.get("status") == "cancelling")
        )
        runtime_cancel = bool(task and task.get("cancel_requested"))
        cancelled = persisted_cancel or runtime_cancel
        status = "cancelled" if cancelled else "error"
        if cancelled:
            message = "已取消"
        elif isinstance(exc, ResearchCancelled):
            message = f"研究任务意外停止：未收到取消请求；{exc}"[:200]
            _LOGGER.exception("Lab worker stopped without a cancellation request task_id=%s", task_id)
        else:
            message = str(exc)[:200]
        if task is not None:
            task.update(
                {"status": status, "message": message, "error": None if cancelled else str(exc)[:500]}
            )
        try:
            _LAB_STORE.update(task_id, status=status, message=message)
        except Exception:  # noqa: BLE001 last-resort logging at worker boundary
            _LOGGER.exception("failed to persist Lab terminal state task_id=%s", task_id)


@router.get("/api/lab/research-status")
def lab_research_status(probe_token: bool = False):
    """研究就绪：日线深度、推荐窗、是否可声称 edge。默认不探 Token（避免拖慢 UI）。"""
    from research_windows import research_status_dict

    return research_status_dict(probe_token=probe_token)


@router.get("/api/lab/catalog")
def lab_catalog():
    """方案说明 + 默认可调参数 + 网格选项（前端研究台用）。"""
    from config import (
        BENCH_EXIT_WINDOW,
        BENCH_MAX_HOLD_DAYS,
        BENCH_STOP_PCT,
        BENCH_STRONG_RESET,
        BENCH_VOL_RATIO_MIN,
        BOX_MAX_AMP,
        BOX_MAX_DAYS,
        BOX_MIN_DAYS,
        BREAKOUT_VOL_RATIO,
        GRID_BENCH,
        PLAN_B_CHG_MIN,
        PLAN_B_CROSS_LOOKBACK,
        PLAN_B_MIN_BUILD_DAYS,
        PLAN_B_REATTACK_RATIO,
    )
    from optimizer import grid_combos

    param_docs = [
        {
            "key": "vol_ratio_min",
            "name": "建仓量比门槛",
            "unit": "倍",
            "meaning": "当日量 / 近5日均量 ≥ 此值 且收阳，才记为建仓放量日。越高越严，信号更少。",
            "affects": "A/B 共用（B 还影响建仓序列识别档位）",
            "default": BENCH_VOL_RATIO_MIN,
            "options": list(GRID_BENCH["vol_ratio_min"]),
            "range_hint": "1.2 ~ 2.0",
        },
        {
            "key": "strong_reset",
            "name": "强势日清零根数",
            "unit": "根",
            "meaning": "持仓期量<标杆的连续强势日达到此数，出货计数清零（洗盘后可重新计）。",
            "affects": "出场（标杆量）",
            "default": BENCH_STRONG_RESET,
            "options": list(GRID_BENCH["strong_reset"]),
            "range_hint": "2 ~ 5",
        },
        {
            "key": "exit_window",
            "name": "二次出货窗口",
            "unit": "交易日",
            "meaning": "窗口内累计 2 次「量≥标杆」出货预警则清仓；超时未达则按规则重计/强平。",
            "affects": "出场（标杆量）",
            "default": BENCH_EXIT_WINDOW,
            "options": list(GRID_BENCH["exit_window"]),
            "range_hint": "5 ~ 20",
        },
        {
            "key": "stop_pct",
            "name": "兜底止损",
            "unit": "比例",
            "meaning": "相对入场价最大回撤；触及则优先止损，压过标杆量出场。0.07 = -7%。",
            "affects": "出场（风控）",
            "default": BENCH_STOP_PCT,
            "options": list(GRID_BENCH["stop_pct"]),
            "range_hint": "0.04 ~ 0.12",
        },
    ]

    strategies = {
        "A": {
            "id": "A",
            "name": "形态突破 + 标杆量出场",
            "tagline": "横盘吸筹平台 → 放量突破上沿 → 标杆量管出场",
            "entry_title": "入场（固定规则，网格不改）",
            "entry_steps": [
                f"箱体横盘 {BOX_MIN_DAYS}~{BOX_MAX_DAYS} 交易日（约 1~6 个月）",
                f"稳健振幅 ≤ {BOX_MAX_AMP:.0%}，支撑/压力多次触及，拒绝单边通道",
                f"近 5 日收盘有效突破阻力 + 放量 ≥ {BREAKOUT_VOL_RATIO}× 箱体均量",
                "涨幅适中、站稳、均线多头（收盘>MA20 且 MA5>MA20）",
                "位置约束：避免下跌中继低位假平台",
            ],
            "exit_title": "出场（网格可调 · 标杆量四象限）",
            "exit_steps": [
                "锁定标杆量：建仓放量序列内倒数第 2 根放量柱的量能",
                "量<标杆且阳 → 拉升(持有)；量<标杆且阴 → 洗盘(持有)",
                "量≥标杆 → 出货预警；窗口内累计 2 次 → 清仓",
                f"连续 {BENCH_STRONG_RESET} 根强势日可清零出货计数（参数可调）",
                f"止损 {BENCH_STOP_PCT:.0%} / 最长持有 {BENCH_MAX_HOLD_DAYS} 日强平",
            ],
            "fixed_note": "入场形态阈值在 config/signals，实验室网格只扫出场相关参数。",
        },
        "B": {
            "id": "B",
            "name": "五步抓主升 + 标杆量出场",
            "tagline": "金叉定趋势 → 建仓辨强弱 → 破五再进攻 → 同套标杆量出场",
            "entry_title": "入场（方案 B · 部分受量比档影响）",
            "entry_steps": [
                f"近 {PLAN_B_CROSS_LOOKBACK} 日发生过 MA5 上穿 MA10（金叉），且收盘 > MA20",
                f"信号日前存在已终止建仓序列，放量柱 ≥ {PLAN_B_MIN_BUILD_DAYS} 根",
                f"破五：当日量 ≥ 标杆量 × {PLAN_B_REATTACK_RATIO}，涨幅 ≥ {PLAN_B_CHG_MIN:.0%}",
                "建仓量比门槛 vol_ratio_min 参与网格（影响建仓识别松紧）",
            ],
            "exit_title": "出场（与 A 相同标杆量体系，可对照）",
            "exit_steps": [
                "同一套：标杆量锁定 → 四象限持有/出货 → 二次出货清仓",
                "同一套：强势清零 / 止损 / 最长持有强平",
                "便于 A/B 对照：只换入场，出场口径一致",
            ],
            "fixed_note": "B 的入场对 vol_ratio_min 敏感；其余出场参数与 A 同网格。",
        },
    }

    n_default = len(grid_combos("A"))
    return {
        "strategies": strategies,
        "params": param_docs,
        "grid_default": GRID_BENCH,
        "grid_combo_count": n_default,
        "defaults": {
            "vol_ratio_min": BENCH_VOL_RATIO_MIN,
            "strong_reset": BENCH_STRONG_RESET,
            "exit_window": BENCH_EXIT_WINDOW,
            "stop_pct": BENCH_STOP_PCT,
            "max_hold_days": BENCH_MAX_HOLD_DAYS,
        },
        "pipeline": [
            {"id": "is", "name": "净成本 IS", "desc": "冻结 IS 第一名，禁止按 OOS 换人"},
            {"id": "oos", "name": "净成本 OOS", "desc": "主候选一次性样本外验证"},
            {"id": "wf", "name": "三窗 WF", "desc": "完整性、交易数、回撤和稳定性"},
            {"id": "base", "name": "双基线", "desc": "固定种子随机与 MA20/60"},
            {"id": "report", "name": "可信报告", "desc": "PASS/FAIL/证据不足；仅隔离候选"},
        ],
        "disclaimer": "研究辅助，不是投资建议。PASS 只登记隔离候选，不会进入 A 池或直接下单。",
    }


@router.post("/api/lab/optimize")
def lab_optimize(req: LabOptimizeRequest):
    """Start, resume or reuse a persistent trusted Lab validation run."""
    from ab_screener.api.routers.legacy_scan import _running_task_id

    running = _running_task_id()
    if running:
        raise HTTPException(status_code=409, detail=f"已有扫描进行中（{running}），优化任务排队等扫描完成")
    lab_run = _lab_running()
    if lab_run:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LAB_TASK_ALREADY_RUNNING",
                "message": "已有优化任务进行中",
                "active_task_id": lab_run,
                "retryable": True,
            },
        )
    windows = _resolve_lab_windows(req)
    if windows.get("mode") == "insufficient":
        raise HTTPException(
            status_code=400,
            detail="日线覆盖不足，无法启动优化。请更新 Token 后 python sync_history.py",
        )
    request_data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    force = bool(request_data.pop("force", False))
    mode = str(request_data.get("mode") or "grid").lower()
    request_data["mode"] = mode
    if mode == "single":
        required = ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
        if any(request_data.get(key) is None for key in required):
            raise HTTPException(status_code=422, detail="单组试跑参数不完整")
        request_data["grid"] = None
    else:
        grid = request_data.get("grid")
        if isinstance(grid, dict):
            clean_grid = {
                key: list(values)[:8]
                for key, values in grid.items()
                if key in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
                and isinstance(values, list)
                and values
            }
            combinations = 1
            for values in clean_grid.values():
                combinations *= len(values)
            if combinations > 120:
                raise HTTPException(status_code=422, detail=f"网格组合过多({combinations}>120)")
            request_data["grid"] = clean_grid or None

    from ab_screener.research.trusted_run import (
        COST_VERSION,
        input_fingerprint,
        prepare_trusted_pit_snapshot,
        trusted_portfolio_identity,
    )

    request_data["portfolio_model"] = trusted_portfolio_identity()
    max_codes = max(20, min(int(request_data.get("max_codes") or 200), 4500))
    pit_snapshot = prepare_trusted_pit_snapshot(
        _store.db_path,
        windows=windows,
        max_codes=max_codes,
    )
    request_data["pit_snapshot"] = pit_snapshot.identity()
    dataset_version = pit_snapshot.dataset_fingerprint
    persisted_request = {**request_data, "_windows": windows}
    input_hash = input_fingerprint(
        request_data,
        windows,
        dataset_version=dataset_version,
        code_version=_BUILD_VERSION,
        cost_version=COST_VERSION,
    )
    if not force:
        cached = _LAB_STORE.completed_by_input_hash(input_hash)
        if cached is not None:
            return {
                "status": "cached",
                "task_id": cached["research_run_id"],
                "strategy": req.strategy,
                "research_mode": windows.get("mode"),
                "can_claim_edge": cached.get("candidate_eligible", False),
                "windows": windows,
            }
        resumable = _LAB_STORE.resumable_by_input_hash(input_hash)
        if resumable is not None:
            task_id = str(resumable["research_run_id"])
            with _LAB_LOCK:
                claimed = _LAB_STORE.resume_run(task_id)
                if not claimed:
                    active = _LAB_STORE.latest_active()
                    active_id = active.get("research_run_id") if active else task_id
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "LAB_TASK_ALREADY_RUNNING",
                            "message": "该实验已由另一个请求恢复",
                            "active_task_id": active_id,
                            "retryable": True,
                        },
                    )
                _LAB_TASKS[task_id] = {
                    "status": "pending",
                    "phase": resumable.get("phase") or "IS",
                    "progress": int(resumable.get("progress") or 0),
                    "message": "从持久化检查点恢复",
                    "started_at": resumable.get("started_at"),
                    "strategy": req.strategy,
                    "windows": windows,
                }
            threading.Thread(target=_run_lab_worker, args=(task_id, req, windows), daemon=True).start()
            return {
                "status": "resumed",
                "task_id": task_id,
                "strategy": req.strategy,
                "research_mode": windows.get("mode"),
                "can_claim_edge": False,
                "windows": windows,
            }
    if len(_LAB_TASKS) > _LAB_TASKS_MAX:
        for tid in [k for k, v in _LAB_TASKS.items() if v.get("status") in ("done", "error", "cancelled")]:
            _LAB_TASKS.pop(tid, None)
            if len(_LAB_TASKS) <= _LAB_TASKS_MAX:
                break
    task_id = uuid.uuid4().hex[:12]
    from ab_screener.research.store import ActiveResearchRunError

    with _LAB_LOCK:
        try:
            _LAB_STORE.create_run(
                task_id,
                strategy=req.strategy,
                research_mode=str(windows.get("mode") or "manual"),
                request=persisted_request,
                input_hash=input_hash,
                dataset_version=dataset_version,
                code_version=_BUILD_VERSION,
                cost_version=COST_VERSION,
                config_hash=input_hash,
            )
        except ActiveResearchRunError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LAB_TASK_ALREADY_RUNNING",
                    "message": "已有优化任务进行中",
                    "active_task_id": exc.active_run_id,
                    "retryable": True,
                },
            ) from exc
        _LAB_TASKS[task_id] = {
            "status": "pending",
            "phase": "IS",
            "progress": 0,
            "message": f"排队中 · {windows.get('label', '')}",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strategy": req.strategy,
            "windows": windows,
        }
    threading.Thread(target=_run_lab_worker, args=(task_id, req, windows), daemon=True).start()
    return {
        "status": "started",
        "task_id": task_id,
        "strategy": req.strategy,
        "research_mode": windows.get("mode"),
        "can_claim_edge": windows.get("can_claim_edge"),
        "windows": windows,
    }


@router.get("/api/lab/status")
def lab_status(task_id: str | None = None):
    if task_id:
        persisted = _LAB_STORE.get(task_id)
        if persisted is not None:
            return _lab_public_record(persisted)
        runtime = _LAB_TASKS.get(task_id)
        if runtime is not None:
            return {"task_id": task_id, **runtime}
        raise HTTPException(status_code=404, detail="任务不存在")

    persisted_active = _LAB_STORE.latest_active()
    if persisted_active is not None:
        return _lab_public_record(persisted_active)
    persisted_latest = _LAB_STORE.latest()
    if persisted_latest is not None:
        return _lab_public_record(persisted_latest)

    selected = _select_lab_task(_LAB_TASKS)
    if selected is not None:
        selected_id, selected_task = selected
        return {"task_id": selected_id, **selected_task}
    return {"task_id": None, "status": "idle"}


def _report_payload(record: dict) -> dict:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    report = result.get("trusted_report") if isinstance(result, dict) else None
    return {
        "research_run_id": record.get("research_run_id"),
        "status": record.get("status"),
        "strategy": record.get("strategy"),
        "research_mode": record.get("research_mode"),
        "verdict": record.get("verdict"),
        "candidate_eligible": bool(record.get("candidate_eligible")),
        "created_at": record.get("created_at"),
        "finished_at": record.get("finished_at"),
        "report_sha256": record.get("report_sha256"),
        "report": report,
    }


@router.get("/api/lab/reports/latest")
def lab_latest_report():
    reports = _LAB_STORE.list_reports(limit=1)
    if not reports:
        raise HTTPException(status_code=404, detail="暂无可信研究报告")
    return _report_payload(reports[0])


@router.get("/api/lab/reports")
def lab_reports(limit: int = 20):
    return {
        "items": [
            {key: value for key, value in _report_payload(record).items() if key != "report"}
            for record in _LAB_STORE.list_reports(limit=limit)
        ]
    }


@router.get("/api/lab/reports/{research_run_id}")
def lab_report(research_run_id: str):
    record = _LAB_STORE.get(research_run_id)
    if record is None or not record.get("report_markdown"):
        raise HTTPException(status_code=404, detail="报告不存在")
    return _report_payload(record)


@router.get("/api/lab/reports/{research_run_id}/download")
def lab_report_download(research_run_id: str, format: str = "markdown"):
    record = _LAB_STORE.get(research_run_id)
    if record is None or not record.get("report_markdown"):
        raise HTTPException(status_code=404, detail="报告不存在")
    if format.lower() == "json":
        body = json.dumps(_report_payload(record), ensure_ascii=False, indent=2, default=str)
        media_type = "application/json"
        filename = f"lab-report-{research_run_id}.json"
    elif format.lower() in ("markdown", "md"):
        body = str(record["report_markdown"])
        media_type = "text/markdown; charset=utf-8"
        filename = f"lab-report-{research_run_id}.md"
    else:
        raise HTTPException(status_code=422, detail="format 仅支持 markdown 或 json")
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/lab/{task_id}/cancel")
def lab_cancel(task_id: str):
    """取消正在运行的优化任务。"""
    from scan_runtime import is_terminal

    with _LAB_LOCK:
        persisted = _LAB_STORE.get(task_id)
        if persisted is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if is_terminal(persisted.get("status")) or persisted.get("status") == "interrupted":
            return {
                "status": persisted["status"],
                "msg": "任务已结束，无需取消",
                "task_id": task_id,
            }
        persisted = _LAB_STORE.request_cancel(task_id)
        t = _LAB_TASKS.get(task_id)
        if t is not None:
            t["cancel_requested"] = True
            t["status"] = "cancelling"
            t["message"] = "取消中…正在停止工作进程"
    return {
        "status": persisted["status"],
        "task_id": task_id,
        "msg": "取消请求已持久化，正在停止工作进程",
    }


@router.get("/api/lab/leaderboard")
def lab_leaderboard(kind: str = "IS", strategy: str = "A", limit: int = 20):
    """Net-cost leaderboard from the latest persistent Lab result."""
    done = [t for t in _LAB_TASKS.values() if t.get("status") == "done" and t.get("result")]
    in_memory = max(done, key=lambda t: t.get("finished_at") or "") if done else None
    persisted = _LAB_STORE.latest()
    result = in_memory.get("result") if in_memory else None
    if persisted and persisted.get("status") == "done" and persisted.get("result"):
        result = persisted["result"]
    if not isinstance(result, dict):
        return {"rows": [], "source": "empty"}
    rows = result.get("is_top" if kind.upper() == "IS" else "oos") or []
    filtered = [row for row in rows if not strategy or row.get("strategy") == strategy]
    return {"rows": filtered[: max(1, min(limit, 100))], "source": "persistent_trusted_run"}


@router.get("/api/lab/compare")
def lab_compare(ids: str = ""):
    """A/B 方案 + fixed/bench 出场对比。ids 逗号分隔 param_id；空则返回最近 done 任务的 A/B 摘要。"""
    from local_store import LocalStore

    st = LocalStore()
    if ids:
        pid_list = [p for p in ids.split(",") if p]
        rows = []
        for pid in pid_list:
            r = st.load_strategy_params()
            hit = r[r["param_id"] == pid]
            if not hit.empty:
                rows.append(hit.iloc[0].to_dict())
        return {"rows": rows}
    # 汇总最近优化任务的 A/B 最佳组合
    done = [t for t in _LAB_TASKS.values() if t.get("status") == "done" and t.get("result")]
    out = {}
    for strat in ("A", "B"):
        tasks = [t for t in done if t.get("strategy") == strat]
        if tasks:
            latest = max(tasks, key=lambda t: t.get("finished_at") or "")
            is_top = latest["result"].get("is_top") or []
            out[strat] = is_top[0] if is_top else None
    return {"best_by_strategy": out}


@router.get("/api/lab/arena")
def lab_arena():
    """擂台赛状态看板：strategy_params 全量（active/candidate/retired）。"""
    from local_store import LocalStore

    df = LocalStore().load_strategy_params()
    if df.empty:
        return {"rows": [], "weights": {}}
    weights = {}
    act = df[df["status"] == "active"]
    for _, r in act.iterrows():
        if r.get("oos_profit_factor"):
            weights[r["strategy"]] = float(r["oos_profit_factor"])
    return {"rows": df.to_dict("records"), "weights": weights}
