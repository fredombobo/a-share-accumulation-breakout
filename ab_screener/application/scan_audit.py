"""Atomic completion and immutable audit records for scanner runs."""
from __future__ import annotations

import hashlib
import json
import math
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
    return _sha256([tuple(row) for row in partitions])


def read_dataset_version(db_path: str | Path, as_of: str = '99991231') -> str:
    """Read-only snapshot of the committed data manifest visible at call time."""
    with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=10) as conn:
        return _dataset_version(conn, as_of)


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


QUALIFICATION_VERSION = 1
QUALIFICATION_SCOPE = "DAILY_SCAN_QUALIFIED_V1"


def build_qualification_report(candidates: Sequence[Mapping[str, Any]], rules_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Top-independent identity of the complete eligibility snapshot."""
    normalized = sorted((dict(row) for row in candidates), key=lambda row: str(row.get("ts_code") or ""))
    counts = {"A": 0, "B": 0}
    groups = {"A": 0, "B": 0, "DATA_INCOMPLETE": 0}
    for row in normalized:
        pool, _ = _pool_and_tier(row.get("reasons"))
        if pool not in counts:
            raise ValueError("qualified candidate has no valid pool")
        counts[pool] += 1
        group = row.get("observation_group", pool)
        if group not in groups:
            raise ValueError("qualified candidate has invalid observation group")
        groups[group] += 1
    return {"version": QUALIFICATION_VERSION, "scope": QUALIFICATION_SCOPE, "counts": counts,
            "groups": groups, "total": len(normalized), "rules_snapshot": dict(rules_snapshot),
            "hash": _sha256({"candidates": normalized, "rules_snapshot": dict(rules_snapshot)})}


def candidate_data_missing_fields(row: Mapping[str, Any], as_of: str, *, serialized: bool = True) -> list[str]:
    """Shared quote/basic evidence checks; numeric strategy thresholds are unchanged."""
    fields = {"price": "最新价", "mv_yi": "总市值(亿)", "pe": "PE(TTM)", "pb": "PB", "turnover": "换手率%"}
    missing = []
    for canonical, display in fields.items():
        value = row.get(canonical if serialized else display)
        try:
            valid = value is not None and not isinstance(value, bool) and math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            missing.append(canonical)
    if str(row.get("quote_as_of") or "") != as_of:
        missing.append("quote_as_of")
    return missing


def _candidate_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    evidence = {key: value for key, value in row.items() if key not in {"created_at", "pool", "display_note"}}
    evidence["reasons"] = re.sub(r"^\[池[AB]\|", "[池*|", str(row.get("reasons") or ""))
    return evidence


def _validate_qualification(result: Mapping[str, Any], final: list[dict], as_of: str) -> tuple[list[dict] | None, dict | None]:
    if "qualified_candidates" not in result:
        if result.get("qualification_report") is not None:
            raise ValueError("qualification report has no complete payload")
        return None, None
    rows, report = result["qualified_candidates"], result.get("qualification_report")
    if not isinstance(rows, list) or not isinstance(report, dict) or not isinstance(report.get("rules_snapshot"), dict):
        raise TypeError("qualified payload/report is incomplete")
    seen = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("qualified row must be an object")
        code = str(row.get("ts_code") or "")
        pool, tier = _pool_and_tier(row.get("reasons"))
        if not code or code in seen or str(row.get("trade_date")) != as_of or pool not in {"A", "B"}:
            raise ValueError("invalid/duplicate qualified candidate or date")
        if row.get("pool") != pool or row.get("tier") != tier or row.get("qualified_pool") != pool:
            raise ValueError("qualified pool/tier lineage mismatch")
        fresh = result.get("freshness") or {}
        regime = result.get("regime") or {}
        if pool == "A" and (tier != "strict" or fresh.get("can_publish_a") is not True or regime.get("allow_new_entries") is not True):
            raise ValueError("qualified A requires strict/current data/market eligibility")
        actual_missing = candidate_data_missing_fields(row, as_of)
        reported_missing = row.get("data_missing_fields") or []
        if actual_missing and (not isinstance(reported_missing, list) or not set(actual_missing) <= set(reported_missing)):
            raise ValueError("qualified quote/basic missing evidence mismatch")
        incomplete = bool(actual_missing) or tier == "data_incomplete" or bool(reported_missing) or not isinstance(row.get("fund_window"), dict) or row["fund_window"].get("complete") is not True
        expected_group = "DATA_INCOMPLETE" if incomplete else pool
        if row.get("observation_group") != expected_group or (pool == "A" and incomplete):
            raise ValueError("qualified data quality group mismatch")
        seen[code] = row
    computed = build_qualification_report(rows, report["rules_snapshot"])
    if report != computed:
        raise ValueError("qualified counts/rules/hash mismatch")
    for row in final:
        code = str(row.get("ts_code") or "")
        if code not in seen or _candidate_evidence(row) != _candidate_evidence(seen[code]):
            raise ValueError("final candidate is not an evidence-identical qualified subset")
        pool, _ = _pool_and_tier(row.get("reasons"))
        if row.get("pool") != pool or (pool == "A" and seen[code]["qualified_pool"] != "A"):
            raise ValueError("final pool cannot promote an ineligible observation")
    return rows, computed


def hash_scan_result(
    summary: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *, qualification: Mapping[str, Any] | None = None,
) -> str:
    """Hash business output while excluding its persistence timestamp."""
    normalized = [
        {key: value for key, value in candidate.items() if key != "created_at"}
        for candidate in candidates
    ]
    payload = {"summary": dict(summary), "candidates": normalized}
    if qualification is not None:
        payload["qualification"] = dict(qualification)
    return _sha256(payload)


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

        # Only a child-owned payload can certify a new publication. Legacy callers
        # remain readable but are explicitly unverified, never promoted silently.
        owned_candidates = result.get('scan_candidates')
        candidates = list(owned_candidates) if owned_candidates is not None else _final_candidates(conn, as_of)
        if owned_candidates is not None:
            if len(candidates) != count_a + count_b:
                raise ValueError('scan candidate count does not match child result')
            seen: set[str] = set()
            counts = {'A': 0, 'B': 0}
            for candidate in candidates:
                code = str(candidate.get('ts_code') or '')
                pool, tier = _pool_and_tier(candidate.get('reasons'))
                if not code or code in seen or str(candidate.get('trade_date')) != as_of or pool not in counts:
                    raise ValueError('invalid/duplicate candidate or publication date/pool mismatch')
                if pool == 'A' and tier != 'strict':
                    raise ValueError('only strict candidates may be published in A')
                seen.add(code)
                counts[pool] += 1
            if counts != {'A': count_a, 'B': count_b}:
                raise ValueError('candidate pool counts do not match child result')
        qualified, qualification = _validate_qualification(result, candidates, as_of)
        dataset_version = _dataset_version(conn, as_of)
        if result.get('input_dataset_version') and result['input_dataset_version'] != dataset_version:
            raise ValueError('data manifest changed during scan; rerun on a consistent snapshot')
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
        result_hash = hash_scan_result(summary, candidates, qualification=qualification)
        now = datetime.now(_TZ).isoformat(timespec="seconds")
        snapshot = dict(strategy_snapshot)
        snapshot["_audit"] = {"code_version": code_version}
        if owned_candidates is not None:
            from ab_screener.application.scan_publication import PUBLICATION_VERSION
            from ab_screener.domain.profile import strategy_profile_from_dict
            entry_hash = strategy_profile_from_dict(dict(strategy_snapshot)).entry_hash()
            fresh = dict(result.get('freshness') or {})
            if count_a and fresh.get('can_publish_a') is not True:
                raise ValueError('current data/calendar verification is required to publish A')
            snapshot['_publication'] = {
                'version': PUBLICATION_VERSION, 'entry_hash': entry_hash,
                'state': 'READY' if fresh.get('can_publish_a') and not fresh.get('historical') else 'HISTORICAL' if fresh.get('historical') else 'DATA_BLOCKED',
                'freshness': fresh, 'regime': dict(result.get('regime') or {}),
                'counts': {'A': count_a, 'B': count_b},
                'pool_report': dict(result['pool_report']) if isinstance(result.get('pool_report'), dict) else None,
            }
            if qualification is not None:
                snapshot['_publication']['qualification'] = qualification
            # Compatibility projection and immutable audit publish together.
            # Zero rows still replace the previous snapshot at this date.
            conn.execute('DELETE FROM scan_result WHERE trade_date=?', (as_of,))
            columns = {r[1] for r in conn.execute('PRAGMA table_info(scan_result)')}
            for candidate in candidates:
                values = {k: v for k, v in candidate.items() if k in columns}
                values.setdefault('created_at', now)
                names = list(values)
                conn.execute(f"INSERT INTO scan_result ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", [values[k] for k in names])

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
        for row in qualified or []:
            pool, tier = _pool_and_tier(row.get("reasons"))
            conn.execute(
                "INSERT INTO scan_run_candidates(run_id,ts_code,stage,pool,tier,total_score,reject_reason,payload_json) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, row["ts_code"], "qualified", pool, tier, row.get("total_score"), None, _canonical_json(row)),
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
        try:
            from ab_screener.application.forward_observations import capture_scan_publication
            capture_scan_publication(db_path, run_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Forward capture failed after committed scan %s", run_id)
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
