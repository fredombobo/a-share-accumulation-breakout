"""事件模型与过滤：公司行为/公告时间线事件（只读）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TimelineEvent:
    ts_code: str
    event_date: str      # ex_date / 公告日（业务日期）
    kind: str            # SPLIT / DIVIDEND / RIGHTS / REVERSAL
    available_at: str    # PIT：数据何时可用（+08:00）
    source: str
    status: str          # PENDING / APPLIED / REVERSED
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "event_date": self.event_date,
            "kind": self.kind,
            "available_at": self.available_at,
            "source": self.source,
            "status": self.status,
            "payload": self.payload,
        }


def filter_events(
    events: list[TimelineEvent],
    *,
    kinds: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[TimelineEvent]:
    """按类型/日期范围过滤，按 event_date 升序返回。"""
    out = []
    for e in events:
        if kinds and e.kind not in kinds:
            continue
        if start and e.event_date < start:
            continue
        if end and e.event_date > end:
            continue
        out.append(e)
    return sorted(out, key=lambda e: (e.event_date, e.available_at))
