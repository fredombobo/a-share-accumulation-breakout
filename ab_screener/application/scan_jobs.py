"""持久扫描任务（scan_jobs 表）。

状态机硬约束：
- 终态 CANCELLED/SUCCEEDED/FAILED 不可再被 finish 改写
- cancel_requested=1 的任务禁止 finish(SUCCEEDED)
- requeue_stale 仅处理心跳超时超过 stale_seconds 的任务
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.security import redact_sensitive_text

_TZ = ZoneInfo("Asia/Shanghai")
_DEFAULT_DB = Path(__file__).resolve().parents[2] / "runtime" / "stock_data.db"

QUEUED = "QUEUED"
RUNNING = "RUNNING"
CANCELLING = "CANCELLING"
CANCELLED = "CANCELLED"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
TERMINAL = {CANCELLED, SUCCEEDED, FAILED}


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # 支持带时区或朴素 ISO
        s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        return datetime.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return None


class ScanJobStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or _DEFAULT_DB)

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(str(self.db_path), timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def create(self, *, top_n: int, days: int, task_id: str | None = None) -> str:
        """新建 QUEUED 任务。禁止 OR REPLACE 覆盖已有终态行。"""
        tid = task_id or uuid.uuid4().hex[:12]
        now = _now()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT status FROM scan_jobs WHERE task_id=?", (tid,)
            ).fetchone()
            if existing:
                raise ValueError(f"task_id 已存在: {tid} status={existing[0]}")
            conn.execute(
                """
                INSERT INTO scan_jobs(task_id, status, top_n, days, cancel_requested,
                  created_at, updated_at)
                VALUES (?,?,?,?,0,?,?)
                """,
                (tid, QUEUED, top_n, days, now, now),
            )
        return tid

    def upsert_running(self, task_id: str, *, top_n: int, days: int) -> None:
        """API 线程路径：仅当不存在时插入 RUNNING；已存在终态则拒绝。"""
        now = _now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM scan_jobs WHERE task_id=?", (task_id,)
            ).fetchone()
            if row:
                if row[0] in TERMINAL:
                    raise ValueError(f"不可覆盖终态任务 {task_id}")
                conn.execute(
                    """
                    UPDATE scan_jobs SET status=?, top_n=?, days=?, updated_at=?,
                      started_at=COALESCE(started_at, ?)
                    WHERE task_id=? AND status NOT IN ('CANCELLED','SUCCEEDED','FAILED')
                    """,
                    (RUNNING, top_n, days, now, now, task_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO scan_jobs(task_id, status, top_n, days, cancel_requested,
                      created_at, updated_at, started_at)
                    VALUES (?,?,?,?,0,?,?,?)
                    """,
                    (task_id, RUNNING, top_n, days, now, now, now),
                )

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM scan_jobs WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def latest(self) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scan_jobs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def latest_active(self) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scan_jobs WHERE status IN (?,?,?) "
                "ORDER BY created_at DESC LIMIT 1",
                (QUEUED, RUNNING, CANCELLING),
            ).fetchone()
            return dict(row) if row else None

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        tid = None
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT task_id FROM scan_jobs WHERE status=? ORDER BY created_at LIMIT 1",
                (QUEUED,),
            ).fetchone()
            if not row:
                return None
            tid = row[0]
            now = _now()
            conn.execute(
                """
                UPDATE scan_jobs SET status=?, worker_id=?, started_at=?, updated_at=?, heartbeat_at=?
                WHERE task_id=? AND status=?
                """,
                (RUNNING, worker_id, now, now, now, tid, QUEUED),
            )
        return self.get(tid) if tid else None

    def request_cancel(self, task_id: str) -> dict[str, Any] | None:
        job = self.get(task_id)
        if not job:
            return None
        if job["status"] in TERMINAL:
            return job
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE scan_jobs SET cancel_requested=1, status=?, updated_at=?
                WHERE task_id=? AND status NOT IN ('CANCELLED','SUCCEEDED','FAILED')
                """,
                (CANCELLING, now, task_id),
            )
        return self.get(task_id)

    def heartbeat(self, task_id: str, checkpoint: dict | None = None) -> None:
        job = self.get(task_id)
        if not job or job["status"] in TERMINAL:
            return
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE scan_jobs SET heartbeat_at=?, updated_at=?, checkpoint_json=?
                WHERE task_id=? AND status NOT IN ('CANCELLED','SUCCEEDED','FAILED')
                """,
                (_now(), _now(), json.dumps(checkpoint or {}, ensure_ascii=False), task_id),
            )

    def is_cancel_requested(self, task_id: str) -> bool:
        job = self.get(task_id)
        return bool(job and (job.get("cancel_requested") or job.get("status") == CANCELLING))

    def finish(
        self,
        task_id: str,
        *,
        status: str,
        run_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """进入终态。返回是否实际写入。

        硬规则：
        - 已是终态 → 拒绝改写（返回 False）
        - cancel_requested 或 CANCELLING → 只允许 CANCELLED/FAILED，禁止 SUCCEEDED
        """
        if status not in TERMINAL:
            raise ValueError(f"finish 状态必须是终态，收到 {status}")
        job = self.get(task_id)
        if not job:
            return False
        if job["status"] in TERMINAL:
            return False
        if (job.get("cancel_requested") or job["status"] == CANCELLING) and status == SUCCEEDED:
            # 取消优先：强制落 CANCELLED
            status = CANCELLED
            error_code = error_code or "CANCELLED"
            error_message = error_message or "cancelled before success solidify"
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE scan_jobs SET status=?, run_id=?, error_code=?, error_message=?,
                  finished_at=?, updated_at=?
                WHERE task_id=? AND status NOT IN ('CANCELLED','SUCCEEDED','FAILED')
                """,
                (
                    status,
                    run_id,
                    error_code,
                    redact_sensitive_text(error_message or "")[:500],
                    _now(),
                    _now(),
                    task_id,
                ),
            )
            return cur.rowcount > 0

    def requeue_stale(self, *, stale_seconds: int = 120) -> int:
        """仅 requeue 心跳超时的 RUNNING（无 run_id、无成功固化）。

        CANCELLING 超时 → CANCELLED，不重新排队。
        """
        n = 0
        cutoff = datetime.now(_TZ) - timedelta(seconds=max(1, int(stale_seconds)))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT task_id, status, heartbeat_at, started_at, run_id, cancel_requested "
                "FROM scan_jobs WHERE status IN ('RUNNING','CANCELLING')"
            ).fetchall()
            for r in rows:
                if r["run_id"]:
                    continue
                ref = _parse_iso(r["heartbeat_at"]) or _parse_iso(r["started_at"])
                if ref is None:
                    # 无时间戳：视为不可靠，仅当 started 也无时跳过 requeue（防刚领取）
                    continue
                # 统一为 aware 比较
                if ref.tzinfo is None:
                    ref = ref.replace(tzinfo=_TZ)
                if ref > cutoff:
                    continue  # 未超时
                if r["status"] == CANCELLING or r["cancel_requested"]:
                    conn.execute(
                        """
                        UPDATE scan_jobs SET status=?, finished_at=?, updated_at=?,
                          error_code='CANCELLED', error_message='stale cancelling'
                        WHERE task_id=? AND status NOT IN ('CANCELLED','SUCCEEDED','FAILED')
                        """,
                        (CANCELLED, _now(), _now(), r["task_id"]),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE scan_jobs SET status=?, worker_id=NULL, cancel_requested=0,
                          updated_at=?, heartbeat_at=NULL
                        WHERE task_id=? AND status='RUNNING'
                        """,
                        (QUEUED, _now(), r["task_id"]),
                    )
                n += 1
        return n


def to_api_status(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {"status": "idle", "stage": "无任务", "progress": 0}
    raw = job.get("status") or QUEUED
    mapping = {
        QUEUED: "pending",
        RUNNING: "running",
        CANCELLING: "cancelling",
        CANCELLED: "cancelled",
        SUCCEEDED: "done",
        FAILED: "error",
    }
    cp: dict[str, Any] = {}
    try:
        cp = json.loads(job.get("checkpoint_json") or "{}")
    except Exception:  # noqa: BLE001
        cp = {}
    return {
        "id": job["task_id"],
        "task_id": job["task_id"],
        "status": mapping.get(raw, str(raw).lower()),
        "stage": cp.get("stage") or raw,
        "progress": int(cp.get("progress") or 0),
        "cancel_requested": bool(job.get("cancel_requested")),
        "run_id": job.get("run_id"),
        "error": redact_sensitive_text(job.get("error_message") or "") or None,
        "result": cp.get("result"),
    }


def finish_persisted_scan_failure(
    task_id: str,
    error: str,
    *,
    db_path: str | Path | None = None,
) -> bool:
    """Move the durable scan job to FAILED without overriding a terminal state."""
    return ScanJobStore(db_path or _DEFAULT_DB).finish(
        task_id,
        status=FAILED,
        error_code="SCAN_FAILED",
        error_message=str(error)[:500],
    )
