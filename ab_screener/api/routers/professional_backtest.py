"""Published professional grid backtest API for AB-Screener."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ab_screener.api.deps import get_db_path
from ab_screener.application.strategy_profile_service import (
    ProfileActivationError,
    activate_from_task,
    activate_manual_profile,
    activation_status,
    profile_state,
    reset_profile,
)
from ab_screener.data.strategy_profile_repository import StrategyProfileRepositoryError
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
from ab_screener.research.resilient_absorption import entry_mechanism_catalog
from ab_screener.research.store import ActiveResearchRunError, ResearchRunStore
from build_version import build_version

router = APIRouter(prefix="/api/backtest", tags=["professional-backtest"])
_MODE = "professional_grid"
_LOCK = threading.RLock()
_STORES: dict[str, ResearchRunStore] = {}
_INITIALIZED: set[str] = set()


class ProfileActivationRequest(BaseModel):
    task_id: str
    acknowledge_exploratory: bool = False


class ProfileResetRequest(BaseModel):
    confirm: bool = False


class ManualProfileRequest(BaseModel):
    parameters: dict[str, Any]
    acknowledge_research_only: bool = False


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


def _raise_profile_error(exc: ProfileActivationError | StrategyProfileRepositoryError) -> NoReturn:
    invalid_request_codes = {
        "INVALID_MANUAL_PARAMETERS",
        "MISSING_MANUAL_PARAMETER",
        "UNKNOWN_PARAMETER",
        "INVALID_PARAMETER_VALUE",
        "PARAMETER_OUT_OF_RANGE",
        "EMPTY_PARAMETER_SPACE",
    }
    raise HTTPException(
        status_code=(
            503
            if exc.code == "STRATEGY_PROFILE_SCHEMA_MISSING"
            else 422 if exc.code in invalid_request_codes else 409
        ),
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
        "entry_mechanisms": entry_mechanism_catalog(),
        "research_boundary": "EXPLORATORY_ONLY",
        "paper_trading_enabled": False,
        "live_trading_enabled": False,
    }


@router.get("/universe")
def universe(
    classification: str = Query(default="industry"),
    group: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    try:
        return universe_catalog(
            db_path,
            classification=classification,
            group=group,
            industry=industry,
        )
    except ProfessionalGridError as exc:
        _raise_grid_error(exc)


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
        "can_run": prepared["data_scope"]["can_run"],
        "prepared": prepared,
        "estimated_work": {
            "combinations": prepared["parameter_space"]["count"],
            "stocks": prepared["universe"]["count"],
            "sample_step": prepared["sample_step"],
            "long_running": prepared["parameter_space"]["long_running"],
            "warning_threshold": prepared["parameter_space"][
                "long_running_warning_combinations"
            ],
            "note": (
                "组合数超过常规阈值，可能持续数小时；任务会在后台运行并持久化。"
                if prepared["parameter_space"]["long_running"]
                else "耗时取决于组合数、股票数和采样步长；任务会在后台运行并持久化。"
            ),
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
    if not prepared["data_scope"]["can_run"]:
        _raise_grid_error(ProfessionalGridError(
            "RESEARCH_DATA_SCOPE_INCOMPLETE", "研究及预热数据检查未通过，请查看缺失原因",
            prepared["data_scope"],
        ))
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
    try:
        activation = activation_status(db_path, task)
    except StrategyProfileRepositoryError as exc:
        _raise_profile_error(exc)
    return {"task": task, "profile_activation": activation}


@router.get("/status/{task_id}")
def status(task_id: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    task = _store(db_path).get(task_id)
    if task is None or task.get("research_mode") != _MODE:
        raise HTTPException(status_code=404, detail="专业回测任务不存在")
    try:
        return {**task, "profile_activation": activation_status(db_path, task)}
    except StrategyProfileRepositoryError as exc:
        _raise_profile_error(exc)


@router.get("/profile")
def get_profile(db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    try:
        return profile_state(db_path)
    except StrategyProfileRepositoryError as exc:
        _raise_profile_error(exc)


@router.post("/profile/activate")
def activate_profile(
    body: ProfileActivationRequest,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    task = _store(db_path).get(body.task_id)
    if task is None or task.get("research_mode") != _MODE:
        raise HTTPException(status_code=404, detail="专业回测任务不存在")
    try:
        return activate_from_task(
            db_path,
            task,
            acknowledge_exploratory=body.acknowledge_exploratory,
        )
    except (ProfileActivationError, StrategyProfileRepositoryError) as exc:
        _raise_profile_error(exc)


@router.post("/profile/reset")
def restore_default_profile(
    body: ProfileResetRequest,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    try:
        return reset_profile(db_path, confirm=body.confirm)
    except (ProfileActivationError, StrategyProfileRepositoryError) as exc:
        _raise_profile_error(exc)


@router.post("/profile/manual")
def activate_user_parameters(
    body: ManualProfileRequest,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    try:
        return activate_manual_profile(
            db_path,
            body.parameters,
            acknowledge_research_only=body.acknowledge_research_only,
        )
    except (ProfileActivationError, StrategyProfileRepositoryError) as exc:
        _raise_profile_error(exc)


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
