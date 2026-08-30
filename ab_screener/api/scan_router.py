"""扫描任务 API（持久任务 + 兼容字段）。"""
from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ab_screener.api.deps import default_db_path
from ab_screener.application.scan_jobs import ScanJobStore, to_api_status
from ab_screener.application.scan_spawn import spawn_scan_runner, write_scan_cancel_flag
from ab_screener.data.scan_run_repository import (
    ScanRunNotFound,
    ScanRunSchemaMissing,
    get_scan_run,
    list_scan_runs,
)
from ab_screener.data.strategy_profile_repository import StrategyProfileRepository

router = APIRouter(tags=["scan"])


class ScanRequest(BaseModel):
    top: int = Field(20, ge=5, le=50)
    days: int = Field(160, ge=30, le=400)
    force: bool = False


def _store() -> ScanJobStore:
    return ScanJobStore()


@router.post("/api/scan")
def start_scan(req: ScanRequest) -> dict[str, Any]:
    store = _store()
    latest = store.latest()
    if latest and latest.get("status") in ("QUEUED", "RUNNING", "CANCELLING"):
        raise HTTPException(
            status_code=409,
            detail=f"已有扫描进行中（task_id={latest['task_id']}），请先等待完成或取消后再发起",
        )
    task_id = store.create(top_n=req.top, days=req.days)
    profile_record = StrategyProfileRepository(default_db_path()).effective()
    profile = profile_record["profile"]
    if os.environ.get("SCAN_WORKER_ENABLED", "true").lower() in ("0", "false", "no"):

        def _once() -> None:
            job = store.claim_next("api-inline")
            if not job:
                return
            from pathlib import Path

            root = Path(__file__).resolve().parents[2]
            runtime = root / "runtime"
            tid = job["task_id"]
            profile_path = runtime / f"scan_{tid}.profile.json"
            profile_path.write_text(profile.to_json(), encoding="utf-8")
            spawn_scan_runner(
                task_id=tid,
                top=int(job.get("top_n") or req.top),
                days=int(job.get("days") or req.days),
                progress=runtime / f"scan_{tid}.progress.json",
                result=runtime / f"scan_{tid}.result.json",
                cancel_file=runtime / f"scan_{tid}.cancel",
                profile=profile_path,
                cwd=root,
            )

        threading.Thread(target=_once, daemon=True).start()

    return {
        "status": "started",
        "task_id": task_id,
        "top": req.top,
        "days": req.days,
        "run_id": None,
        "config_hash": profile_record["config_hash"],
        "as_of": None,
        "dataset_version": None,
    }


@router.get("/api/scan/status")
def scan_status(task_id: str | None = None) -> dict[str, Any]:
    store = _store()
    job = store.get(task_id) if task_id else store.latest()
    return to_api_status(job)


@router.post("/api/scan/{task_id}/cancel")
def cancel_scan(task_id: str) -> dict[str, Any]:
    store = _store()
    job = store.request_cancel(task_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    write_scan_cancel_flag(task_id)
    st = to_api_status(job)
    return {
        "status": st["status"],
        "stage": st["stage"],
        "task_id": task_id,
        "cancel_requested": True,
    }


@router.get("/api/scan/runs")
def list_runs(limit: int = 20) -> dict[str, Any]:
    return {"runs": list_scan_runs(default_db_path(), limit)}


@router.get("/api/scan/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    try:
        return get_scan_run(default_db_path(), run_id)
    except ScanRunNotFound:
        raise HTTPException(status_code=404, detail="run not found")
    except ScanRunSchemaMissing as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
