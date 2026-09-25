"""One server-derived next action for the personal daily workflow."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
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


def _latest_date(conn: sqlite3.Connection, table: str) -> str | None:
    if not _table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
    return str(row[0]) if row and row[0] else None


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
    from ab_screener.application.scan_publication import read_scan_publication
    from ab_screener.market_regime import data_freshness

    path = Path(db_path).resolve()
    if not path.is_file():
        return _response("SYNC_DATA", latest_market_date=None, expected_market_date=None,
                         blocker_codes=["MARKET_DATA_MISSING"])
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        # fail-closed：legacy 行情表缺失（如仅迁移了 v2 表的副本）→ 视为数据未就绪。
        latest_market = _latest_date(conn, "daily")
        latest_moneyflow = _latest_date(conn, "moneyflow")
        latest_basic = _latest_date(conn, "daily_basic")
        freshness = data_freshness(latest_market or "", store=SimpleNamespace(db_path=path),
                                   reference_now=now)
        expected_market = freshness.get("expected_as_of") or None
        details = {"latest_market_date": latest_market, "expected_market_date": expected_market,
                   "latest_moneyflow_date": latest_moneyflow, "latest_basic_date": latest_basic,
                   "freshness": freshness}
        blockers = list(freshness.get("blocking_reasons") or [])
        if not freshness.get("is_current"):
            blockers.append("MARKET_DATA_STALE")
        if not expected_market or latest_moneyflow != expected_market:
            blockers.append("MONEYFLOW_DATA_STALE")
        if not expected_market or latest_basic != expected_market:
            blockers.append("DAILY_BASIC_DATA_STALE")
        if blockers:
            return _response("SYNC_DATA", **details, blocker_codes=blockers)

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
                **details,
            )

        try:
            scan = read_scan_publication(path)
        except (sqlite3.Error, ValueError, KeyError, TypeError):
            scan = None
        if not (scan and scan.get("verified") is True and scan.get("state") == "READY"
                and scan.get("as_of") == expected_market):
            return _response(
                "RUN_SCAN",
                trade_date=latest_market,
                **details,
            )

        return _response(
            "DAILY_COMPLETE",
            trade_date=latest_market,
            scan_run_id=str(scan["run_id"]),
            **details,
        )
