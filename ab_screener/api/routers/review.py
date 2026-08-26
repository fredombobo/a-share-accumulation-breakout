"""Review API（P7.1/P7.4）：笔记 / 决策台账 / 周报 / 归因（只读+受控写）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from ab_screener.application import review_service

router = APIRouter(prefix="/api/v2/review", tags=["review"])

_REF_TYPES = ("experiment", "run", "signal", "order", "candidate", "shadow",
              "retirement", "none")
_KINDS = ("idea", "hypothesis", "decision", "log", "weekly")


@router.get("/notes")
def notes(
    ref_type: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    try:
        items = review_service.query_notes(
            db_path, ref_type=ref_type, kind=kind, limit=limit
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"items": items, "count": len(items)}


@router.post("/notes")
def create_note(payload: dict[str, Any], db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    try:
        return review_service.add_note(
            db_path,
            title=str(payload.get("title", "")),
            body=str(payload.get("body", "")),
            ref_type=str(payload.get("ref_type", "none")),
            ref_id=payload.get("ref_id"),
            kind=str(payload.get("kind", "idea")),
            tags=payload.get("tags") if isinstance(payload.get("tags"), list) else None,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_FAILED", "message": str(exc)},
        )


@router.get("/decisions")
def decisions(
    ref_type: str | None = None,
    limit: int = 100,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    try:
        items = review_service.query_decisions(db_path, ref_type=ref_type, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"items": items, "count": len(items)}


@router.post("/decisions")
def create_decision(
    payload: dict[str, Any], db_path: str = Depends(get_db_path)
) -> dict[str, Any]:
    try:
        return review_service.add_decision(
            db_path,
            action=str(payload.get("action", "")),
            rationale=str(payload.get("rationale", "")),
            ref_type=str(payload.get("ref_type", "none")),
            ref_id=payload.get("ref_id"),
            risk_flags=payload.get("risk_flags")
            if isinstance(payload.get("risk_flags"), list) else None,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_FAILED", "message": str(exc)},
        )


@router.get("/weekly")
def weekly(since: str | None = None, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    try:
        return review_service.weekly_report(db_path, since=since)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/attribution")
def attribution(
    start: str | None = None,
    end: str | None = None,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """归因任务状态（只读）；完整归因计算由 CLI run_attribution.py 提供。"""
    from ab_screener.research.attribution import collect_attribution_events
    from local_store import LocalStore
    from research_windows import recommend_research_plan

    try:
        store = LocalStore(db_path=db_path)
        plan = recommend_research_plan(store.distinct_dates("daily"))
        range_start = start or plan.is_start
        range_end = end or plan.oos_end
        events = collect_attribution_events(
            store=store,
            start=range_start,
            end=range_end,
        )
        summary = review_service.attribution_summary(events)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_STATE", "message": f"归因不可用: {exc}"},
        )
    return {
        "side_effects": False,
        "window": {"start": range_start, "end": range_end},
        **summary,
    }
