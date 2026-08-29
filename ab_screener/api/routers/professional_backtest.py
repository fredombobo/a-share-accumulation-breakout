"""Published professional grid backtest API for AB-Screener."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query

from ab_screener.api.deps import get_db_path
from ab_screener.research.condition_plugins import condition_catalog
from ab_screener.research.professional_grid import (
    ProfessionalGridError,
    parameter_catalog,
    universe_catalog,
)
from ab_screener.research.professional_runner import (
    execute_professional_run,
    prepare_professional_request,
)
from ab_screener.research.store import ActiveResearchRunError, ResearchRunStore
from build_version import build_version

router = APIRouter(prefix="/api/backtest", tags=["professional-backtest"])
_MODE = "professional_grid"
_LOCK = threading.RLock()
_STORES: dict[str, ResearchRunStore] = {}
_INITIALIZED: set[str] = set()


def _store(db_path: str) -> ResearchRunStore:
    key = str(Path(db_path).resolve())
    with _LOCK:
        store = _STORES.get(key)
        if store is None:
            store = ResearchRunStore(key)
            _STORES[key] = store
        if key not in _INITIALIZED:
            store.mark_orphaned_interrupted()
            _INITIALIZED.add(key)
        return store


def _raise_grid_error(exc: ProfessionalGridError) -> NoReturn:
    raise HTTPException(
        status_code=422,
        detail={
            "code": exc.code,
            "message": str(exc),
            "details": exc.details,
            "retryable": False,
        },
    ) from exc


@router.get("/catalog")
def catalog(db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    return {
        **parameter_catalog(),
        "conditions": condition_catalog(db_path),
        "research_boundary": "EXPLORATORY_ONLY",
        "paper_trading_enabled": False,
        "live_trading_enabled": False,
    }


@router.get("/universe")
def universe(
    industry: str | None = Query(default=None),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    return universe_catalog(db_path, industry=industry)


@router.post("/preview")
def preview(
    body: dict[str, Any],
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    try:
        prepared = prepare_professional_request(db_path, body)
    except ProfessionalGridError as exc:
        _raise_grid_error(exc)
    return {
        "can_run": True,
        "prepared": prepared,
        "estimated_work": {
            "combinations": prepared["parameter_space"]["count"],
            "stocks": prepared["universe"]["count"],
            "sample_step": prepared["sample_step"],
            "note": "耗时取决于组合数、股票数和采样步长；任务会在后台运行并持久化。",
        },
    }


@router.post("/run")
def run(
    body: dict[str, Any],
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    from ab_screener.research.pit_reader import latest_research_cutoff
    from ab_screener.research.trusted_run import COST_VERSION

    try:
        prepared = prepare_professional_request(db_path, body)
    except ProfessionalGridError as exc:
        _raise_grid_error(exc)
    store = _store(db_path)
    completed = store.completed_by_input_hash(prepared["input_hash"])
    if completed and completed.get("research_mode") == _MODE:
        return {"task_id": completed["task_id"], "status": "done", "cached": True}
    run_id = f"probt-{uuid.uuid4().hex[:12]}"
    try:
        created = store.create_run(
            run_id,
            strategy="A",
            research_mode=_MODE,
            request=prepared,
            input_hash=prepared["input_hash"],
            dataset_version=latest_research_cutoff(db_path),
            code_version=str(build_version()),
            cost_version=COST_VERSION,
            config_hash=prepared["parameter_space"]["sha256"],
        )
    except ActiveResearchRunError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BACKTEST_ALREADY_RUNNING",
                "message": "已有研究任务运行中，请先查看其进度或取消",
                "details": {"task_id": exc.active_run_id},
                "retryable": True,
            },
        ) from exc
    _start_worker(store, db_path, run_id, prepared)
    return {"task_id": created["task_id"], "status": created["status"], "cached": False}


@router.get("/latest")
def latest(db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    task = _store(db_path).latest_for_mode(_MODE)
    return {"task": task}


@router.get("/status/{task_id}")
def status(task_id: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    task = _store(db_path).get(task_id)
    if task is None or task.get("research_mode") != _MODE:
        raise HTTPException(status_code=404, detail="专业回测任务不存在")
    return task


@router.post("/{task_id}/cancel")
def cancel(task_id: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    store = _store(db_path)
    task = store.get(task_id)
    if task is None or task.get("research_mode") != _MODE:
        raise HTTPException(status_code=404, detail="专业回测任务不存在")
    return store.request_cancel(task_id)


def _start_worker(
    store: ResearchRunStore,
    db_path: str,
    run_id: str,
    prepared: dict[str, Any],
) -> None:
    def progress(phase: str, pct: int, message: str) -> None:
        store.update(
            run_id,
            status="running",
            phase=phase,
            progress=max(0, min(100, int(pct))),
            message=message,
            heartbeat_at=__import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    def worker() -> None:
        from optimizer import ResearchCancelled
        from tushare_init import sanitize_error

        try:
            progress("DATA", 1, "准备冻结研究输入")
            result = execute_professional_run(
                db_path,
                prepared,
                progress=progress,
                cancel_check=lambda: store.is_cancel_requested(run_id),
            )
            store.update(
                run_id,
                status="done",
                phase="DONE",
                progress=100,
                message=result.get("verdict_label") or "专业回测完成",
                result=result,
                verdict=result.get("verdict"),
                candidate_eligible=False,
                can_claim_edge=False,
                report_markdown=result.get("report_markdown"),
            )
        except ResearchCancelled:
            store.update(
                run_id,
                status="cancelled",
                phase="CANCELLED",
                message="任务已取消；已完成证据保留在运行记录中",
                candidate_eligible=False,
                can_claim_edge=False,
            )
        except Exception as exc:  # noqa: BLE001
            store.update(
                run_id,
                status="error",
                phase="ERROR",
                message=sanitize_error(exc)[:500],
                candidate_eligible=False,
                can_claim_edge=False,
            )

    threading.Thread(target=worker, daemon=True, name=f"pro-backtest-{run_id[-6:]}").start()
