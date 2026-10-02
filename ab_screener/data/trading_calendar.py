"""Read an independently sourced exchange calendar; never infer it from quotes.

This reader performs no migrations, refreshes, network calls, or writes. Missing
closed-day rows and locally inferred calendars cannot certify daily publication.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

VERIFIED_CALENDAR_SOURCES = frozenset({"tushare", "tushare.trade_cal", "sse", "szse", "bse", "exchange"})


def date_key(value: Any) -> str:
    raw = str(value or "").strip().replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    datetime.strptime(raw, "%Y%m%d")
    return raw


def calendar_window(
    db_path: str | Path | None,
    *,
    today: str,
    as_of: str | None = None,
    before_today: bool = False,
    required_days: int = 5,
) -> dict[str, Any]:
    """Certify the expected completed session and its last N open sessions.

    Every calendar day from the observation/required-window start through today
    must have an authoritative open/closed record. Quotes do not supply dates.
    """
    today = date_key(today)
    observation = date_key(as_of) if as_of else today
    required_days = max(1, int(required_days))
    result: dict[str, Any] = {
        "verified": False, "status": "CALENDAR_UNAVAILABLE", "source": "trade_cal",
        "expected_as_of": "", "required_dates": [], "open_dates": [],
        "missing_dates": [], "unverified_dates": [], "reason": "独立交易日历不可用",
    }
    if db_path is None:
        return result
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return result
    start = min(observation, (datetime.strptime(today, "%Y%m%d") - timedelta(days=max(45, required_days * 7))).strftime("%Y%m%d"))
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)) as conn, conn:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT cal_date,is_open,source FROM trade_cal WHERE cal_date>=? AND cal_date<=? ORDER BY cal_date",
                (start, today),
            ).fetchall()
    except sqlite3.Error:
        return result
    calendar: dict[str, tuple[bool, str]] = {}
    invalid: set[str] = set()
    for raw_date, flag, source in rows:
        try:
            key = date_key(raw_date)
        except ValueError:
            continue
        if key in calendar or flag not in (0, 1, "0", "1"):
            invalid.add(key)
        calendar[key] = (flag in (1, "1"), str(source or "").strip().lower())
    opens = sorted(key for key, (is_open, _) in calendar.items() if is_open and (key < today or not before_today))
    required = opens[-required_days:]
    result["open_dates"] = opens
    result["required_dates"] = required
    result["expected_as_of"] = opens[-1] if opens else ""
    earliest = min(observation, required[0]) if required else min(observation, today)
    cursor = datetime.strptime(earliest, "%Y%m%d")
    end = datetime.strptime(today, "%Y%m%d")
    missing, unverified = [], []
    while cursor <= end:
        key = cursor.strftime("%Y%m%d")
        if key not in calendar:
            missing.append(key)
        elif key in invalid or calendar[key][1] not in VERIFIED_CALENDAR_SOURCES:
            unverified.append(key)
        cursor += timedelta(days=1)
    result["missing_dates"] = missing
    result["unverified_dates"] = unverified
    if missing:
        result.update(status="CALENDAR_INCOMPLETE", reason=f"独立交易日历缺少 {len(missing)} 个日期的开闭市记录")
    elif unverified:
        result.update(status="CALENDAR_UNVERIFIED", reason="交易日历包含推断、来源未知或无效记录")
    elif len(required) < required_days:
        result.update(status="CALENDAR_WINDOW_INCOMPLETE", reason=f"交易日历不足 {required_days} 个已完成交易日")
    else:
        result.update(verified=True, status="READY", reason="独立交易日历覆盖完整")
    return result
