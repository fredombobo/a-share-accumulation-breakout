"""数据来源状态（只读）：分区清单、覆盖率、新鲜度。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ab_screener.data.intelligence_repository import dataset_status


def data_source_status(db_path: str | Path) -> dict[str, Any]:
    """各数据集来源状态：分区/行数/最近入库 + daily 新鲜度与活跃覆盖。"""
    status = dataset_status(db_path)
    latest_daily = None
    active_total = None
    active_covered = None
    with sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "daily" in tables:
            latest_daily = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        if "instrument_universe_rules" in tables:
            active_total = conn.execute(
                "SELECT COUNT(*) FROM instrument_universe_rules WHERE security_type='stock'"
            ).fetchone()[0]
            if latest_daily:
                active_covered = conn.execute(
                    "SELECT COUNT(DISTINCT ts_code) FROM daily WHERE trade_date=?",
                    (latest_daily,),
                ).fetchone()[0]
    return {
        "datasets": status,
        "daily_latest_trade_date": latest_daily,
        "active_stock_coverage": (
            {"total": int(active_total), "covered_latest": int(active_covered or 0),
             "pct": round(100.0 * int(active_covered or 0) / int(active_total), 2)}
            if active_total else None
        ),
    }
