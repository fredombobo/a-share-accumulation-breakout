"""Review 决策台账服务（P7.4）：笔记/决策/周报/归因薄封装。

- 所有写操作带校验；引用统一使用 run/signal/order/experiment IDs。
- 归因只读转发 ab_screener.research.attribution（不创建订单/信号）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ab_screener.data.research_note_repository import (
    create_decision,
    create_note,
    list_decisions,
    list_notes,
    weekly_digest,
)
from ab_screener.research.attribution import summarize_attribution

_REF_TYPES = ("experiment", "run", "signal", "order", "candidate", "shadow",
              "retirement", "none")


def open_conn(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def add_note(
    db_path: str | Path,
    *,
    title: str,
    body: str = "",
    ref_type: str = "none",
    ref_id: str | None = None,
    kind: str = "idea",
    tags: list[str] | None = None,
    created_by: str = "user",
) -> dict[str, Any]:
    with open_conn(db_path) as conn:
        return create_note(
            conn, title=title, body=body, ref_type=ref_type, ref_id=ref_id,
            kind=kind, tags=tags, created_by=created_by,
        )


def query_notes(
    db_path: str | Path,
    *,
    ref_type: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with open_conn(db_path) as conn:
        return list_notes(conn, ref_type=ref_type, kind=kind, limit=limit)


def add_decision(
    db_path: str | Path,
    *,
    action: str,
    rationale: str,
    ref_type: str = "none",
    ref_id: str | None = None,
    risk_flags: list[str] | None = None,
    created_by: str = "user",
) -> dict[str, Any]:
    with open_conn(db_path) as conn:
        return create_decision(
            conn, action=action, rationale=rationale, ref_type=ref_type,
            ref_id=ref_id, risk_flags=risk_flags, created_by=created_by,
        )


def query_decisions(
    db_path: str | Path,
    *,
    ref_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with open_conn(db_path) as conn:
        return list_decisions(conn, ref_type=ref_type, limit=limit)


def weekly_report(db_path: str | Path, *, since: str | None = None) -> dict[str, Any]:
    with open_conn(db_path) as conn:
        return weekly_digest(conn, since=since)


def attribution_summary(events: list[Any]) -> dict[str, Any]:
    """归因汇总（只读；输入为 AttributionEvent 列表）。"""
    if not events:
        return {"count": 0, "message": "无归因事件"}
    return summarize_attribution(events)
