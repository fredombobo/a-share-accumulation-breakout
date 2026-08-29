"""One server-derived next action for the personal daily workflow."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

_COPY: dict[str, tuple[str, str, str, str | None]] = {
    "SYNC_DATA": ("行情需要更新", "本地行情还没有覆盖应完成的最新交易日。", "同步最新行情", None),
    "WAIT_SCAN": ("扫描正在运行", "不需要再次启动；系统会保留当前进度。", "查看扫描进度", "/"),
    "RUN_SCAN": ("行情已就绪", "今天还没有对应数据版本的选股结果。", "开始今日扫描", "/"),
    "DAILY_COMPLETE": ("今日选股已完成", "行情和扫描结果已就绪，可查看候选证据或进入专业回测。", "查看今日候选", "/"),
}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _expected_market_date(conn: sqlite3.Connection, now: datetime) -> str | None:
    today = now.astimezone(_TZ).strftime("%Y%m%d")
    before_close = now.astimezone(_TZ).hour < 16
    if _table_exists(conn, "trade_cal"):
        operator = "<" if before_close else "<="
        row = conn.execute(
            f"SELECT cal_date FROM trade_cal WHERE is_open=1 AND cal_date {operator} ? "
            "ORDER BY cal_date DESC LIMIT 1",
            (today,),
        ).fetchone()
        if row:
            return str(row[0])
    if _table_exists(conn, "daily"):
        row = conn.execute(
            "SELECT MAX(trade_date) FROM daily WHERE trade_date<=?", (today,)
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    return None


def _response(action: str, **details: Any) -> dict[str, Any]:
    title, reason, primary_label, href = _COPY[action]
    return {
        "next_action": action,
        "title": title,
        "reason": reason,
        "primary_label": primary_label,
        "href": href,
        **details,
    }


def build_today_guide(
    db_path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Derive exactly one action without changing any business state."""
    now = now or datetime.now(_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_TZ)
    with sqlite3.connect(str(db_path), timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        # fail-closed：legacy 行情表缺失（如仅迁移了 v2 表的副本）→ 视为数据未就绪。
        latest_market: str | None = None
        if _table_exists(conn, "daily"):
            latest_row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
            latest_market = str(latest_row[0]) if latest_row and latest_row[0] else None
        expected_market = _expected_market_date(conn, now)

        if latest_market is None or (expected_market and latest_market < expected_market):
            return _response(
                "SYNC_DATA",
                latest_market_date=latest_market,
                expected_market_date=expected_market,
                blocker_codes=["MARKET_DATA_STALE"],
            )

        active_scan = None
        if _table_exists(conn, "scan_jobs"):
            active_scan = conn.execute(
                "SELECT task_id,status FROM scan_jobs "
                "WHERE status IN ('QUEUED','RUNNING','CANCELLING') "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if active_scan is not None:
            return _response(
                "WAIT_SCAN",
                task_id=str(active_scan["task_id"]),
                task_status=str(active_scan["status"]),
                latest_market_date=latest_market,
                expected_market_date=expected_market,
            )

        scan = None
        if _table_exists(conn, "scan_runs"):
            scan = conn.execute(
                "SELECT run_id,result_hash FROM scan_runs "
                "WHERE as_of=? AND status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1",
                (latest_market,),
            ).fetchone()
        if scan is None:
            return _response(
                "RUN_SCAN",
                trade_date=latest_market,
                latest_market_date=latest_market,
                expected_market_date=expected_market,
            )

        return _response(
            "DAILY_COMPLETE",
            trade_date=latest_market,
            scan_run_id=str(scan["run_id"]),
            latest_market_date=latest_market,
            expected_market_date=expected_market,
        )
