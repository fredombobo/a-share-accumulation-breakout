"""调度仓库（P6.1）：run/step 持久化、attempt 保留、续跑、租约。"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from ab_screener.operations.dag import idempotency_key

_TZ = ZoneInfo("Asia/Shanghai")


class SchedulerError(RuntimeError):
    """调度仓库错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _require(conn: sqlite3.Connection, table: str) -> None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not has:
        raise SchedulerError(
            f"{table} 表不存在：先运行 scripts/migrate_v2.py --apply（fail-closed）"
        )


def step_attempt_status(
    conn: sqlite3.Connection, *, key: str
) -> tuple[int, str]:
    """同幂等键的已完成 attempt 数；含 SUCCESS 则返回 (attempt, 'SUCCESS')。"""
    _require(conn, "dag_step_runs")
    row = conn.execute(
        "SELECT attempt, status FROM dag_step_runs WHERE step_run_id LIKE ?"
        " AND status='SUCCESS' ORDER BY attempt DESC LIMIT 1",
        (f"{key}-%",),
    ).fetchone()
    if row is None:
        return (0, "NONE")
    return (int(row[0]), str(row[1]))


def start_run(
    conn: sqlite3.Connection, *, trade_date: str, mode: str = "EOD"
) -> str:
    """创建 run（同 trade_date+mode 已有 run → 幂等返回既有，续跑语义）。"""
    _require(conn, "dag_runs")
    existing = conn.execute(
        "SELECT run_id FROM dag_runs WHERE trade_date=? AND mode=?"
        " ORDER BY created_at DESC LIMIT 1", (trade_date, mode),
    ).fetchone()
    if existing:
        return existing[0]
    run_id = hashlib.sha256(f"{trade_date}|{mode}".encode()).hexdigest()[:16]
    conn.execute(
        "INSERT INTO dag_runs (run_id, trade_date, mode, status, created_at)"
        " VALUES (?,?,?,'RUNNING',?)",
        (run_id, trade_date, mode, _now()),
    )
    conn.commit()
    return run_id


def record_step_attempt(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    trade_date: str,
    step_name: str,
    scope_type: str,
    scope_id: str,
    input_hash: str,
    attempt: int,
    status: str,
    error: str = "",
) -> str:
    """记录步骤 attempt（每个 attempt 一行：RUNNING→终态 UPDATE，不重复插入）。"""
    key = idempotency_key(trade_date, step_name, scope_type, scope_id, input_hash)
    step_run_id = f"{key}-{attempt}"
    now = _now()
    existing = conn.execute(
        "SELECT 1 FROM dag_step_runs WHERE step_run_id=?", (step_run_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE dag_step_runs SET status=?, finished_at=?, error=? WHERE step_run_id=?",
            (status,
             now if status in ("SUCCESS", "FAIL", "ATTEMPT_FAILED") else None,
             error, step_run_id),
        )
    else:
        conn.execute(
            "INSERT INTO dag_step_runs (step_run_id, run_id, trade_date, step_name,"
            " scope_type, scope_id, input_hash, attempt, status, started_at, finished_at, error)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (step_run_id, run_id, trade_date, step_name, scope_type, scope_id, input_hash,
             attempt, status, now if status == "RUNNING" else None,
             now if status in ("SUCCESS", "FAIL", "ATTEMPT_FAILED") else None, error),
        )
    conn.commit()
    return key


def mark_run_finished(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    conn.execute(
        "UPDATE dag_runs SET status=?, finished_at=? WHERE run_id=?",
        (status, _now(), run_id),
    )
    conn.commit()


def last_completed_steps(conn: sqlite3.Connection, run_id: str) -> set[str]:
    """本 run 已成功步骤（崩溃续跑用）。"""
    rows = conn.execute(
        "SELECT DISTINCT step_name FROM dag_step_runs"
        " WHERE run_id=? AND status='SUCCESS'", (run_id,),
    ).fetchall()
    return {r[0] for r in rows}


def acquire_lease(
    conn: sqlite3.Connection, *, lease_id: str, holder: str, trade_date: str, ttl_seconds: int = 300
) -> bool:
    """租约抢占（防并发调度）；过期可重取。"""
    _require(conn, "dag_leases")
    expires = datetime.now(_TZ).timestamp() + ttl_seconds
    existing = conn.execute(
        "SELECT holder, expires_at FROM dag_leases WHERE lease_id=?", (lease_id,)
    ).fetchone()
    if existing:
        if existing[0] == holder:
            return True
        import datetime as _dt

        try:
            exp = _dt.datetime.fromisoformat(existing[1])
        except ValueError:
            exp = _dt.datetime.min.replace(tzinfo=_TZ)  # 无时区 → 视为已过期（可抢占）
        if exp.timestamp() > datetime.now(_TZ).timestamp():
            return False  # 被他人持有且未过期
        # 过期 → 抢占
    conn.execute(
        "INSERT INTO dag_leases (lease_id, holder, trade_date, expires_at)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(lease_id) DO UPDATE SET holder=excluded.holder,"
        " trade_date=excluded.trade_date, expires_at=excluded.expires_at",
        (lease_id, holder, trade_date,
         datetime.fromtimestamp(expires, _TZ).isoformat(timespec="seconds")),
    )
    conn.commit()
    return True
