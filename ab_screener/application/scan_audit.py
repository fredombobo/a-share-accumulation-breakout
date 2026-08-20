"""Atomic completion and immutable audit records for scanner runs."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.application.scan_jobs import CANCELLED, CANCELLING, SUCCEEDED, TERMINAL

_TZ = ZoneInfo("Asia/Shanghai")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _dataset_version(conn: sqlite3.Connection, as_of: str) -> str:
    partitions: list[tuple[Any, ...]] = []
    if _table_exists(conn, "dataset_partitions"):
        partitions = conn.execute(
            """
            SELECT dataset, trade_date, row_count, content_sha256, revision, ingested_at
            FROM dataset_partitions
            WHERE trade_date <= ?
            ORDER BY dataset, trade_date
            """,
            (as_of,),
        ).fetchall()
    return _sha256(partitions)


def _universe(conn: sqlite3.Connection) -> list[str]:
    if not _table_exists(conn, "stock_basic"):
        return []
    return [
        str(row[0])
        for row in conn.execute("SELECT ts_code FROM stock_basic ORDER BY ts_code").fetchall()
    ]


def _final_candidates(conn: sqlite3.Connection, as_of: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "scan_result"):
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM scan_result WHERE trade_date=? ORDER BY ts_code",
        (as_of,),
    ).fetchall()
    return [dict(row) for row in rows]


def _pool_and_tier(reasons: object) -> tuple[str | None, str | None]:
    match = re.search(r"\[池([AB])\|([^\]|]+)", str(reasons or ""))
    if match:
        return match.group(1), match.group(2).strip()
    return None, None


def hash_scan_result(
    summary: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> str:
    """Hash business output while excluding its persistence timestamp."""
    normalized = [
        {key: value for key, value in candidate.items() if key != "created_at"}
        for candidate in candidates
    ]
    return _sha256({"summary": dict(summary), "candidates": normalized})


def record_orphaned_successes(db_path: str | Path, *, code_version: str) -> list[str]:
    """Append explicit invalid audit rows for legacy premature-success jobs.

    This never reconstructs a successful result. It preserves the anomaly so the
    history remains traceable while preventing an unreported success-without-run.
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    repaired: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        jobs = conn.execute(
            """
            SELECT j.* FROM scan_jobs j
            LEFT JOIN scan_runs r ON r.run_id=j.run_id
            WHERE j.status='SUCCEEDED' AND j.run_id IS NOT NULL AND r.run_id IS NULL
            ORDER BY j.created_at, j.task_id
            """
        ).fetchall()
        as_of = ""
        if _table_exists(conn, "scan_result"):
            row = conn.execute("SELECT MAX(trade_date) FROM scan_result").fetchone()
            as_of = str(row[0] or "") if row else ""
        for job in jobs:
            run_id = str(job["run_id"])
            task_id = str(job["task_id"])
            job_payload = dict(job)
            now = datetime.now(_TZ).isoformat(timespec="seconds")
            anomaly = {
                "reason": "legacy success was exposed before its run audit committed",
                "code_version": code_version,
                "job": job_payload,
            }
            conn.execute(
                """
                INSERT INTO scan_runs(
                  run_id, task_id, as_of, strategy_snapshot_json, config_hash, git_sha,
                  dataset_version, random_seed, input_hash, result_hash, research_mode,
                  status, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    task_id,
                    as_of,
                    _canonical_json({"_audit": anomaly}),
                    "",
                    code_version,
                    _dataset_version(conn, as_of),
                    None,
                    _sha256({"task_id": task_id, "top_n": job["top_n"], "days": job["days"]}),
                    _sha256(anomaly),
                    "unknown",
                    "INVALID_ORPHAN",
                    now,
                ),
            )
            repaired.append(task_id)
        conn.commit()
        return repaired
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_scan_run(
    db_path: str | Path,
    *,
    run_id: str,
    task_id: str,
    as_of: str,
    days: int,
    result: Mapping[str, Any],
    count_a: int,
    count_b: int,
    strategy_snapshot: Mapping[str, Any],
    config_hash: str,
    code_version: str,
    research_mode: str,
    random_seed: int | None = None,
) -> bool:
    """Commit the run audit and ``SUCCEEDED`` job state in one transaction.

    Cancellation wins while the transaction is being acquired. A caller may only
    expose success after this function returns ``True``.
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute("SELECT * FROM scan_jobs WHERE task_id=?", (task_id,)).fetchone()
        if job is None:
            raise ValueError(f"unknown scan task: {task_id}")

        status = str(job["status"])
        existing = conn.execute(
            "SELECT task_id, status FROM scan_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if status == SUCCEEDED and existing is not None:
            if str(existing["task_id"]) != task_id or str(existing["status"]) != SUCCEEDED:
                raise ValueError(f"run_id conflict: {run_id}")
            conn.commit()
            return True
        if status in TERMINAL:
            conn.rollback()
            return False
        if bool(job["cancel_requested"]) or status == CANCELLING:
            now = datetime.now(_TZ).isoformat(timespec="seconds")
            conn.execute(
                """
                UPDATE scan_jobs
                SET status=?, error_code='CANCELLED', error_message=?, finished_at=?, updated_at=?
                WHERE task_id=?
                """,
                (CANCELLED, "cancelled before success audit", now, now, task_id),
            )
            conn.commit()
            return False

        candidates = _final_candidates(conn, as_of)
        dataset_version = _dataset_version(conn, as_of)
        input_payload = {
            "as_of": as_of,
            "days": int(days),
            "universe": _universe(conn),
            "config_hash": config_hash,
            "dataset_version": dataset_version,
        }
        summary = {
            "total_candidates": int(result.get("total_candidates") or 0),
            "hits": int(result.get("hits") or 0),
            "count_a": int(count_a),
            "count_b": int(count_b),
        }
        result_hash = hash_scan_result(summary, candidates)
        now = datetime.now(_TZ).isoformat(timespec="seconds")
        snapshot = dict(strategy_snapshot)
        snapshot["_audit"] = {"code_version": code_version}

        conn.execute(
            """
            INSERT INTO scan_runs(
              run_id, task_id, as_of, strategy_snapshot_json, config_hash, git_sha,
              dataset_version, random_seed, input_hash, result_hash, research_mode,
              status, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                task_id,
                as_of,
                _canonical_json(snapshot),
                config_hash,
                code_version,
                dataset_version,
                random_seed,
                _sha256(input_payload),
                result_hash,
                research_mode,
                SUCCEEDED,
                now,
            ),
        )

        funnel = (
            ("prefilter", summary["total_candidates"]),
            ("hits", summary["hits"]),
            ("pool_A", summary["count_a"]),
            ("pool_B", summary["count_b"]),
        )
        for stage, size in funnel:
            conn.execute(
                """
                INSERT INTO scan_run_candidates(
                  run_id, ts_code, stage, pool, tier, total_score, reject_reason, payload_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (run_id, f"_agg_{stage}", stage, None, None, float(size), None, _canonical_json({"n": size})),
            )
        for row in candidates:
            pool, tier = _pool_and_tier(row.get("reasons"))
            conn.execute(
                """
                INSERT INTO scan_run_candidates(
                  run_id, ts_code, stage, pool, tier, total_score, reject_reason, payload_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    str(row.get("ts_code") or ""),
                    "final",
                    pool,
                    tier,
                    row.get("total_score"),
                    None,
                    _canonical_json(row),
                ),
            )

        updated = conn.execute(
            """
            UPDATE scan_jobs
            SET status=?, run_id=?, error_code=NULL, error_message='', finished_at=?, updated_at=?
            WHERE task_id=? AND status NOT IN ('CANCELLED','SUCCEEDED','FAILED')
              AND cancel_requested=0
            """,
            (SUCCEEDED, run_id, now, now, task_id),
        )
        if updated.rowcount != 1:
            raise RuntimeError(f"scan task did not transition to success: {task_id}")
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
