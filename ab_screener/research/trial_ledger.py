"""trial 账本（P3.1）：完整 trial 历史（含失败/取消/拒绝）+ 参数空间覆盖。"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from ab_screener.research.registry import ResearchGovernanceError


def trial_history(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    statuses: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """实验的全部 trial（默认含 FAILED/CANCELLED/REJECTED）。"""
    sql = (
        "SELECT trial_id, experiment_id, params_json, status, outcome_json, created_at"
        " FROM research_trials WHERE experiment_id=?"
    )
    params: list[Any] = [experiment_id]
    if statuses is not None:
        s_list = list(statuses)
        placeholders = ",".join("?" * len(s_list))
        sql += f" AND status IN ({placeholders})"
        params.extend(s_list)
    rows = conn.execute(sql + " ORDER BY created_at", params).fetchall()
    import json

    out = []
    for r in rows:
        out.append(
            {
                "trial_id": r[0],
                "experiment_id": r[1],
                "params": json.loads(r[2]),
                "status": r[3],
                "outcome": json.loads(r[4]) if r[4] else None,
                "created_at": r[5],
            }
        )
    return out


def status_counts(conn: sqlite3.Connection, experiment_id: str) -> dict[str, int]:
    """按状态统计（失败/取消/拒绝必须可见，不静默丢弃）。"""
    rows = conn.execute(
        "SELECT status, COUNT(*) FROM research_trials WHERE experiment_id=?"
        " GROUP BY status",
        (experiment_id,),
    ).fetchall()
    counts = {r[0]: int(r[1]) for r in rows}
    for status in ("COMPLETED", "FAILED", "CANCELLED", "REJECTED", "PENDING", "RUNNING"):
        counts.setdefault(status, 0)
    return counts


def parameter_space_coverage(
    conn: sqlite3.Connection, experiment_id: str
) -> dict[str, Any]:
    """已试参数组合数 / 失败组合数（网格覆盖率诊断）。"""
    try:
        history = trial_history(conn, experiment_id)
    except ResearchGovernanceError:
        return {"trials": 0, "distinct_params": 0, "failed_or_rejected": 0}
    distinct = {(tuple(sorted(p.items()))) for t in history for p in [t["params"]]}
    failed = [t for t in history if t["status"] in ("FAILED", "CANCELLED", "REJECTED")]
    return {
        "trials": len(history),
        "distinct_params": len(distinct),
        "failed_or_rejected": len(failed),
    }
