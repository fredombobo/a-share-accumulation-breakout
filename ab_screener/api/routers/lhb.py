"""龙虎榜研究 API（T10）。只读；金额单位元；research_only。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ab_screener.api.deps import get_db_path
from ab_screener.application import lhb_query
from ab_screener.domain.lhb_contracts import ACTOR_TYPE_VALUES, SIGNAL_STATUS_VALUES

router = APIRouter(prefix="/api/v2/lhb", tags=["lhb"])


@router.get("/radar")
def radar(
    trade_date: str = Query(..., min_length=8, max_length=8),
    seat_id: str | None = None,
    actor_type: str | None = Query(None, description="actor 类型"),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    as_of: str | None = Query(None, description="PIT knowledge cutoff；默认当前已知"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    if actor_type is not None and actor_type not in ACTOR_TYPE_VALUES:
        raise HTTPException(status_code=422, detail=f"非法 actor_type: {actor_type}")
    return lhb_query.radar(
        db_path,
        trade_date,
        seat_id=seat_id,
        actor_type=actor_type,
        min_confidence=min_confidence,
        as_of=as_of,
        limit=limit,
        offset=offset,
    )


@router.get("/events")
def events(
    trade_date: str | None = None,
    ts_code: str | None = None,
    seat_id: str | None = None,
    actor_type: str | None = Query(None, description="actor 类型"),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    as_of: str | None = Query(None, description="PIT knowledge cutoff"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    if actor_type is not None and actor_type not in ACTOR_TYPE_VALUES:
        raise HTTPException(status_code=422, detail=f"非法 actor_type: {actor_type}")
    return lhb_query.events(
        db_path,
        trade_date=trade_date,
        ts_code=ts_code,
        seat_id=seat_id,
        actor_type=actor_type,
        min_confidence=min_confidence,
        as_of=as_of,
        limit=limit,
        offset=offset,
    )


@router.get("/seats/{seat_id}")
def seat_detail(
    seat_id: str,
    as_of: str = Query(..., description="Asia/Shanghai ISO timestamp"),
    window_days: int = Query(60),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    return lhb_query.seat_profile(db_path, seat_id, as_of=as_of, window_days=window_days)


@router.get("/actors/{actor_id}")
def actor_detail(
    actor_id: str,
    as_of: str = Query(...),
    window_days: int = Query(60),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    return lhb_query.actor_profile(db_path, actor_id, as_of=as_of, window_days=window_days)


@router.get("/stocks/{ts_code}/timeline")
def stock_timeline(
    ts_code: str,
    limit: int = Query(100, ge=1, le=500),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    return lhb_query.stock_timeline(db_path, ts_code, limit=limit)


@router.get("/network")
def network(
    trade_date: str = Query(..., min_length=8, max_length=8),
    as_of: str | None = Query(None, description="PIT knowledge cutoff；默认当前已知"),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    return lhb_query.network(db_path, trade_date=trade_date, as_of=as_of)


@router.get("/quality")
def quality(
    trade_date: str = Query(..., min_length=8, max_length=8),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    return lhb_query.quality(db_path, trade_date)


@router.get("/signals")
def signals(
    trade_date: str | None = None,
    ts_code: str | None = None,
    status: str | None = None,
    as_of: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    if status is not None and status not in SIGNAL_STATUS_VALUES:
        raise HTTPException(status_code=422, detail=f"非法 signal status: {status}")
    return lhb_query.signals(
        db_path,
        trade_date=trade_date,
        ts_code=ts_code,
        status=status,
        as_of=as_of,
        limit=limit,
        offset=offset,
    )


@router.get("/backtest")
def backtest(db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    return lhb_query.backtest_summary(db_path)
