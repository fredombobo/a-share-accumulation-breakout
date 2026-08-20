"""研究治理 API（P7.1）：实验登记 / 正式验证运行 / 取消（受控写 + 只读）。"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from ab_screener.data.db import SchemaMissing
from ab_screener.research.registry import (
    list_experiments,
    register_experiment_at,
    require_experiment_at,
)
from ab_screener.research.store import ResearchRunStore

router = APIRouter(prefix="/api/v2/research", tags=["research"])


@router.get("/experiments")
def experiments(limit: int = 100, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    try:
        items = list_experiments(db_path, limit)
    except SchemaMissing:
        raise HTTPException(status_code=404, detail="v2:research_governance 未迁移")
    return {"items": items, "count": None}


@router.post("/experiments")
def create_experiment(
    payload: dict[str, Any], db_path: str = Depends(get_db_path)
) -> dict[str, Any]:
    strategy = str(payload.get("strategy", "")).strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    config_hash = str(payload.get("config_hash", "")).strip()
    if not strategy or not config_hash:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_FAILED", "message": "strategy 与 config_hash 必填"},
        )
    experiment_id = register_experiment_at(
        db_path, strategy=strategy, params=params, config_hash=config_hash
    )
    return {"experiment_id": experiment_id, "status": "REGISTERED"}


@router.post("/experiments/{experiment_id}/runs")
def start_run(
    experiment_id: str, payload: dict[str, Any], db_path: str = Depends(get_db_path)
) -> dict[str, Any]:
    """启动正式验证（幂等：同 input_hash 已完成则返回既有 run）。"""
    try:
        exp = require_experiment_at(db_path, experiment_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    store = ResearchRunStore(db_path)
    input_hash = str(payload.get("input_hash", "")).strip()
    if input_hash:
        done = store.completed_by_input_hash(input_hash)
        if done is not None:
            return {"research_run_id": done["research_run_id"], "reused": True}
    run_id = uuid.uuid4().hex[:16]
    created = store.create_run(
        run_id,
        strategy=exp["strategy"],
        research_mode=str(payload.get("research_mode", "full")),
        request=payload,
        input_hash=input_hash or run_id,
        dataset_version=str(payload.get("dataset_version", "unknown")),
        code_version=str(payload.get("code_version", "unknown")),
        cost_version=str(payload.get("cost_version", "unknown")),
        config_hash=str(exp.get("config_hash", "")),
    )
    return {"research_run_id": created["research_run_id"], "reused": False}


@router.get("/runs/{run_id}")
def run_status(run_id: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    run = ResearchRunStore(db_path).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="研究运行不存在")
    return run


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    try:
        run = ResearchRunStore(db_path).request_cancel(run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"research_run_id": run["research_run_id"], "status": run["status"]}
