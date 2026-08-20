"""ScanProfile API router（P4.2，独立 router，不挂载共享 app——P7 集成时挂载）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from ab_screener.data.scan_profile_repository import (
    get_profile_at,
    list_active_profiles_at,
    save_profile_at,
)
from ab_screener.domain.scan_profile import ScanProfile

router = APIRouter(prefix="/api/v2/scan-profiles", tags=["scan-profiles"])


@router.get("")
def list_profiles(db_path: str = Depends(get_db_path)) -> list[dict[str, Any]]:
    return [p.to_dict() for p in list_active_profiles_at(db_path)]


@router.get("/{profile_id}")
def read_profile(profile_id: str, db_path: str = Depends(get_db_path), version: str | None = None) -> dict[str, Any]:
    profile = get_profile_at(db_path, profile_id, version)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile 不存在")
    return profile.to_dict()


@router.post("")
def create_profile(payload: dict[str, Any], db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    try:
        profile = ScanProfile(
            name=payload["name"],
            version=payload["version"],
            strategy_ids=tuple(payload["strategy_ids"]),
            configs=payload["configs"],
            status=payload.get("status", "DRAFT"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    save_profile_at(db_path, profile)
    return profile.to_dict()
