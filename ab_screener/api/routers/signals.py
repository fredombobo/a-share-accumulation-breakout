"""信号 API router（P4.2，独立 router，不挂载共享 app——P7 集成时挂载）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from ab_screener.data.db import SchemaMissing
from ab_screener.data.signal_repository import (
    get_observation_at,
    list_observations_at,
    outcomes_at,
)

router = APIRouter(prefix="/api/v2/signals", tags=["signals"])


@router.get("")
def list_observations(
    db_path: str = Depends(get_db_path),
    strategy: str | None = None,
    status: str | None = None,
    trade_date: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """按形态/状态/日期分页查询观察（含生命周期投影状态）。"""
    try:
        items = list_observations_at(
            db_path, strategy=strategy, status=status, trade_date=trade_date, limit=limit
        )
    except SchemaMissing:
        raise HTTPException(status_code=404, detail="信号表未迁移")
    return {"items": items, "count": len(items)}


@router.get("/observations/{observation_id}")
def read_observation(observation_id: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    row = get_observation_at(db_path, observation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="观察不存在")
    return row


@router.get("/observations/{observation_id}/outcomes")
def read_outcomes(observation_id: str, db_path: str = Depends(get_db_path)) -> list[dict[str, Any]]:
    return outcomes_at(db_path, observation_id)
