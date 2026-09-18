"""Prospective price-observation API; writes only its isolated sidecar."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ab_screener.api.deps import get_db_path
from ab_screener.application.forward_observations import (
    ForwardObservationError,
    capture_scan_publication,
    enable_forward_observations,
    forward_history,
    forward_results,
    forward_status,
    refresh_forward_observations,
)

router = APIRouter(prefix="/api/forward", tags=["forward-observations"])


class CaptureRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)


class RefreshRequest(BaseModel):
    run_id: str | None = Field(default=None, min_length=1, max_length=120)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ForwardObservationError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail={
            "code": "FORWARD_STORE_UNAVAILABLE", "message": "观察记录暂不可用，扫描发布状态不受影响",
        }) from exc


@router.get("/status")
def status(db_path: str = Depends(get_db_path)):
    return _call(forward_status, db_path)


@router.post("/enable")
def enable(db_path: str = Depends(get_db_path)):
    return _call(enable_forward_observations, db_path)


@router.post("/capture")
def capture(body: CaptureRequest, db_path: str = Depends(get_db_path)):
    return _call(capture_scan_publication, db_path, body.run_id)


@router.post("/refresh")
def refresh(body: RefreshRequest, db_path: str = Depends(get_db_path)):
    return _call(refresh_forward_observations, db_path, body.run_id)


@router.get("/history")
def history(limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
            db_path: str = Depends(get_db_path)):
    return _call(forward_history, db_path, limit=limit, offset=offset)


@router.get("/results")
def results(run_id: str | None = None, horizon: int | None = None,
            limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
            all_revisions: bool = False, db_path: str = Depends(get_db_path)):
    return _call(forward_results, db_path, run_id=run_id, horizon=horizon, limit=limit, offset=offset,
                 all_revisions=all_revisions)
