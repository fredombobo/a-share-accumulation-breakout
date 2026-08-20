"""扫描运行只读查询（API 层禁止直连 sqlite）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ScanRunNotFound(LookupError):
    """run_id 不存在。"""


class ScanRunSchemaMissing(RuntimeError):
    """scan_runs 表未迁移。"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def list_scan_runs(db_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM scan_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_scan_run(db_path: str | Path, run_id: str) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM scan_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not row:
            raise ScanRunNotFound(run_id)
        funnel = conn.execute(
            "SELECT stage, COUNT(*) AS n, MAX(total_score) AS score "
            "FROM scan_run_candidates WHERE run_id=? GROUP BY stage",
            (run_id,),
        ).fetchall()
        return {"run": dict(row), "funnel": [dict(f) for f in funnel]}
    except sqlite3.OperationalError as exc:
        raise ScanRunSchemaMissing(str(exc)) from exc
    finally:
        conn.close()


def schema_max_version(db_path: str | Path) -> int | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0] or 0) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def active_scan_worker(db_path: str | Path) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        hb = conn.execute(
            "SELECT worker_id, heartbeat_at, status FROM scan_jobs "
            "WHERE status IN ('RUNNING','QUEUED','CANCELLING') "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not hb:
            return None
        return {"worker_id": hb[0], "heartbeat_at": hb[1], "status": hb[2]}
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
