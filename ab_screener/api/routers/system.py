"""系统 API（P7.1）：健康/备份/审计（只读，无删除/改写 API）。"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from ab_screener.application.audit_service import list_audit_events
from ab_screener.data.db import SchemaMissing
from ab_screener.operations.backup import backup_ok, latest_backup
from ab_screener.operations.health import system_health

router = APIRouter(prefix="/api/v2/system", tags=["system"])


def _backup_root() -> str | None:
    """backup_root：只读 AB_BACKUP_ROOT；未设置 → None。

    不得悄悄把 runtime/backups 当通过；未配置时明确 BACKUP_ROOT_UNCONFIGURED。
    HTTP 查询参数不得作为生产 backup_root 覆盖来源（防越权）。
    """
    return os.environ.get("AB_BACKUP_ROOT")


@router.get("/health")
def health(
    port: int = 8001,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """DB/WAL/磁盘/DAG/端口身份。backup_root 未配置 → BACKUP_ROOT_UNCONFIGURED。"""
    root = _backup_root()
    return system_health(db_path, root, port=port)


@router.get("/backups")
def backups() -> dict[str, Any]:
    """备份及恢复演练状态（backup_root 只取 AB_BACKUP_ROOT；未配置 → BACKUP_ROOT_UNCONFIGURED）。"""
    root = _backup_root()
    if root is None:
        return {"backup_root": None, "latest": None, "status": {"status": "BACKUP_ROOT_UNCONFIGURED"}}
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
