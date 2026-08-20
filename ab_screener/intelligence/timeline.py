"""公告/公司行为时间线（只读）：PIT 事件 + 状态投影。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ab_screener.intelligence.events import TimelineEvent, filter_events


def corporate_action_timeline(
    db_path: str | Path,
    ts_code: str,
    *,
    start: str | None = None,
    end: str | None = None,
    kinds: set[str] | None = None,
) -> list[TimelineEvent]:
    """公司行为时间线：账本事件 + 状态投影；携带 available_at（PIT）。"""
    with sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30) as conn:
        rows = conn.execute(
            "SELECT a.ts_code, a.ex_date, a.kind, a.available_at, a.source,"
            " COALESCE(s.status,'PENDING'), a.payload_json"
            " FROM corporate_actions a"
            " LEFT JOIN corporate_action_status s"
            " ON a.corporate_action_id = s.corporate_action_id"
            " WHERE a.ts_code=? ORDER BY a.ex_date, a.available_at",
            (ts_code,),
        ).fetchall()
    import json

    events = [
        TimelineEvent(
            ts_code=r[0], event_date=r[1], kind=r[2], available_at=r[3],
            source=r[4], status=r[5], payload=json.loads(r[6]),
        )
        for r in rows
    ]
    return filter_events(events, kinds=kinds, start=start, end=end)


def timeline_summary(events: list[TimelineEvent]) -> dict[str, Any]:
    return {
        "count": len(events),
        "kinds": sorted({e.kind for e in events}),
        "first_event_date": events[0].event_date if events else None,
        "last_event_date": events[-1].event_date if events else None,
    }
