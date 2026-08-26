"""legacy 数据同步路由（G2 拆路由第 4 步）。
共享状态从 ab_screener.api.legacy_state import；领域模块（paper_trading / research /
scan_spawn 等）函数内延迟 import，保持与原实现一致。
"""
from __future__ import annotations

import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException

from ab_screener.api.legacy_state import (
    _SYNC_LOCK,
    _SYNC_STATE,
)

router = APIRouter(tags=["legacy"])

# ═══════════════════════════════════════════════════════════
# 数据同步 API（手动更新行情，2026-08-16 新增）
# ═══════════════════════════════════════════════════════════


@router.post("/api/sync")
def sync_start():
    """触发增量行情同步（后台执行；已有同步进行中返回 409）。"""
    global _SYNC_STATE
    with _SYNC_LOCK:
        if _SYNC_STATE.get("status") == "running":
            raise HTTPException(status_code=409, detail="行情同步已在进行中")
        _SYNC_STATE = {
            "status": "running",
            "message": "开始同步行情…",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "latest_daily": None,
            "latest_moneyflow": None,
            "failed_dates": [],
        }

    def _run() -> None:
        from tushare_init import sanitize_error

        try:
            from local_store import sync_from_tushare

            res = sync_from_tushare(days_back=30, verbose=False)
            failed = (res.get("failed_daily_dates") or []) + (res.get("failed_moneyflow_dates") or [])
            with _SYNC_LOCK:
                _SYNC_STATE.update(
                    status="done" if not failed else "error",
                    message=(
                        f"同步完成：daily 新增 {len(res.get('daily_dates') or [])} 个交易日、"
                        f"moneyflow 新增 {len(res.get('moneyflow_dates') or [])} 个交易日"
                        + (f"；{len(failed)} 个日期失败（可重试）" if failed else "")
                    ),
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                    latest_daily=res.get("latest_daily"),
                    latest_moneyflow=res.get("latest_moneyflow"),
                    failed_dates=failed[:20],
                )
        except Exception as exc:  # noqa: BLE001
            with _SYNC_LOCK:
                _SYNC_STATE.update(
                    status="error",
                    message=f"同步失败：{sanitize_error(exc)[:200]}",
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )

    threading.Thread(target=_run, daemon=True, name="data-sync").start()
    return {"status": "running", "message": "同步已开始"}


@router.get("/api/sync/status")
def sync_status():
    with _SYNC_LOCK:
        return dict(_SYNC_STATE)


