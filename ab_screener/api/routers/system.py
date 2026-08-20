"""系统 API（P7.1）：健康/备份/审计（只读，无删除/改写 API）。"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import default_db_path, get_db_path
from ab_screener.application.audit_service import list_audit_events
from ab_screener.data.db import SchemaMissing
from ab_screener.operations.backup import backup_ok, latest_backup
from ab_screener.operations.health import system_health

router = APIRouter(prefix="/api/v2/system", tags=["system"])


def _backup_root(explicit: str | None) -> str:
    """backup_root 解析：显式参数 → AB_BACKUP_ROOT → runtime/backups。"""
    return explicit or os.environ.get("AB_BACKUP_ROOT") or str(
        default_db_path().parent / "backups"
    )


@router.get("/health")
def health(
    port: int = 8001,
    backup_root: str | None = None,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """DB/WAL/磁盘/DAG/端口身份。"""
    return system_health(db_path, _backup_root(backup_root), port=port)


@router.get("/backups")
def backups(backup_root: str | None = None) -> dict[str, Any]:
    """备份及恢复演练状态（backup_root 缺省取 AB_BACKUP_ROOT 或 runtime/backups）。"""
    root = _backup_root(backup_root)
    latest = latest_backup(root)
    status = backup_ok(root)
    return {"backup_root": root, "latest": latest, "status": status}


@router.get("/audit")
def audit(db_path: str = Depends(get_db_path), limit: int = 100) -> dict[str, Any]:
    """审计查询（只读；无删除/改写 API）。"""
    try:
        events = list_audit_events(db_path, limit)
    except SchemaMissing:
        raise HTTPException(status_code=404, detail="审计表未迁移")
    return {"events": events, "count": len(events)}
