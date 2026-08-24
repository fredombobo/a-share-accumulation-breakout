"""调度仓库（V2R-O2）：run/step 持久化、attempt 保留、崩溃续跑、原子租约。

修复项：
- `acquire_lease` SELECT→UPSERT 置于 `BEGIN IMMEDIATE` 原子事务（防并发抢占竞态）。
- `step_attempt_status` 读取全部已记录 attempt（不只 SUCCESS）：重启后从
  已记录 attempt 之后继续，失败 attempt 不会从 1 重数而越过 max_attempts。
- 新增 `renew_lease` / `release_lease` / `lease_status` 支撑租约生命周期。
"""
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


def _now_timestamp() -> float:
    return datetime.now(_TZ).timestamp()


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
    """返回 (已记录 attempt 数, 状态)。

    状态语义（供 SchedulerRunner 续跑决策）：
    - NONE           → 无任何 attempt，从 1 开始；
    - SUCCESS        → 已有成功 attempt，幂等跳过；
    - ATTEMPT_FAILED → 末次为失败，续跑从 attempt+1 开始（attempt 保留不重数）；
    - RUNNING        → 末次 RUNNING（进程崩溃遗留），续跑从同一 attempt 覆盖重试；
    - EXHAUSTED      → 末次 FAIL 且已达 max_attempts，禁止再执行（第四次不得执行）。
    """
    _require(conn, "dag_step_runs")
    rows = conn.execute(
        "SELECT attempt, status FROM dag_step_runs WHERE step_run_id LIKE ?"
        " ORDER BY attempt ASC",
        (f"{key}-%",),
    ).fetchall()
    if not rows:
        return (0, "NONE")
    last_attempt = int(rows[-1][0])
    if any(r[1] == "SUCCESS" for r in rows):
        return (last_attempt, "SUCCESS")
    last_status = str(rows[-1][1])
    if last_status == "FAIL":
        return (last_attempt, "EXHAUSTED")
    if last_status in ("RUNNING", "PENDING"):
        return (last_attempt, "RUNNING")
    return (last_attempt, "ATTEMPT_FAILED")


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


def last_completed_steps(
    conn: sqlite3.Connection, run_id: str, *, input_hash: str | None = None
) -> set[str]:
    """本 run 已成功步骤（崩溃续跑用）。指定 input_hash 时仅计入同输入身份的成功。"""
    if input_hash is None:
        rows = conn.execute(
            "SELECT DISTINCT step_name FROM dag_step_runs"
            " WHERE run_id=? AND status='SUCCESS'", (run_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT step_name FROM dag_step_runs"
            " WHERE run_id=? AND input_hash=? AND status='SUCCESS'", (run_id, input_hash),
        ).fetchall()
    return {r[0] for r in rows}


# ── 租约（包围控制：并发单租约，原子抢占，过期可接管，退出释放） ──


def _lease_expires_iso(ttl_seconds: int) -> str:
    return datetime.fromtimestamp(_now_timestamp() + ttl_seconds, _TZ).isoformat(
        timespec="seconds"
    )


def _lease_expired(expires_at: str) -> bool:
    try:
        exp = datetime.fromisoformat(expires_at)
    except ValueError:
        return True  # 无时区/非法 → 视为已过期（可抢占）
    return exp.timestamp() <= _now_timestamp()


def acquire_lease(
    conn: sqlite3.Connection, *, lease_id: str, holder: str, trade_date: str,
    ttl_seconds: int = 300,
) -> bool:
    """原子抢占租约（BEGIN IMMEDIATE）；同 holder 续期；他人未过期拒绝；过期可接管。"""
    _require(conn, "dag_leases")
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT holder, expires_at FROM dag_leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
        if existing and existing[0] == holder:
            conn.execute(
                "UPDATE dag_leases SET expires_at=?, trade_date=? WHERE lease_id=?",
                (_lease_expires_iso(ttl_seconds), trade_date, lease_id),
            )
            conn.commit()
            return True
        if existing and not _lease_expired(existing[1]):
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO dag_leases (lease_id, holder, trade_date, expires_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(lease_id) DO UPDATE SET holder=excluded.holder,"
            " trade_date=excluded.trade_date, expires_at=excluded.expires_at",
            (lease_id, holder, trade_date, _lease_expires_iso(ttl_seconds)),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def renew_lease(
    conn: sqlite3.Connection, *, lease_id: str, holder: str, ttl_seconds: int = 300
) -> bool:
    """同 holder 续租（延长过期时间）；他人持有 → False。"""
    _require(conn, "dag_leases")
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT holder FROM dag_leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
        if not existing or existing[0] != holder:
            conn.rollback()
            return False
        conn.execute(
            "UPDATE dag_leases SET expires_at=? WHERE lease_id=?",
            (_lease_expires_iso(ttl_seconds), lease_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def release_lease(
    conn: sqlite3.Connection, *, lease_id: str, holder: str
) -> bool:
    """退出释放（仅持有者可释放）；无租约/非持有者 → False。"""
    _require(conn, "dag_leases")
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT holder FROM dag_leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
        if not existing or existing[0] != holder:
            conn.rollback()
            return False
        conn.execute("DELETE FROM dag_leases WHERE lease_id=?", (lease_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def lease_status(conn: sqlite3.Connection, lease_id: str) -> dict | None:
    _require(conn, "dag_leases")
    row = conn.execute(
        "SELECT lease_id, holder, trade_date, expires_at FROM dag_leases"
        " WHERE lease_id=?", (lease_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "lease_id": row[0], "holder": row[1], "trade_date": row[2],
        "expires_at": row[3], "expired": _lease_expired(row[3]),
    }


def lease_id_for(trade_date: str, scope_type: str, scope_id: str) -> str:
    blob = hashlib.sha256(
        f"dag-lease|{trade_date}|{scope_type}|{scope_id}".encode()
    ).hexdigest()[:16]
    return blob
