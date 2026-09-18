"""Read immutable scanner publications, including successful empty runs."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

PUBLICATION_VERSION = 2


def read_scan_publication(db_path: str | Path, run_id: str | None = None, *, stage: str = "final") -> dict[str, Any] | None:
    if stage not in {"final", "qualified"}:
        raise ValueError("scan stage must be final or qualified")
    with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scan_runs'").fetchone():
            return None
        if run_id:
            row = conn.execute("SELECT rowid, * FROM scan_runs WHERE run_id=? AND status='SUCCEEDED'", (run_id,)).fetchone()
        else:
            row = conn.execute("SELECT rowid, * FROM scan_runs WHERE status='SUCCEEDED' ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        if row is None:
            return None
        snapshot = json.loads(row['strategy_snapshot_json'] or '{}')
        publication = snapshot.get('_publication') or {}
        def candidates_at(wanted):
            return [json.loads(item[0]) for item in conn.execute(
                "SELECT payload_json FROM scan_run_candidates WHERE run_id=? AND stage=? ORDER BY total_score DESC, ts_code",
                (row['run_id'], wanted),
            )]
        candidates = candidates_at('final')
        qualification_declared = 'qualification' in publication
        raw_qualification = publication.get('qualification')
        qualification = raw_qualification if isinstance(raw_qualification, dict) else {}
        from ab_screener.application.scan_audit import (
            QUALIFICATION_SCOPE,
            QUALIFICATION_VERSION,
            _validate_qualification,
        )
        available = bool(publication.get('version') == PUBLICATION_VERSION and
                         qualification.get('version') == QUALIFICATION_VERSION and qualification.get('scope') == QUALIFICATION_SCOPE)
        qualified, qualified_verified, integrity_error = [], False, None
        if qualification_declared and not available:
            integrity_error = "Declared qualification protocol has unsupported version/scope or invalid metadata"
        if available:
            try:
                qualified = candidates_at('qualified')
                _validate_qualification({'qualified_candidates': qualified, 'qualification_report': qualification,
                                         'freshness': publication.get('freshness'), 'regime': publication.get('regime')},
                                        candidates, row['as_of'])
                qualified_verified = True
            except (ValueError, TypeError, KeyError) as exc:
                integrity_error = str(exc)
                qualified = []
        started_at = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='scan_jobs'").fetchone():
            columns = {item[1] for item in conn.execute('PRAGMA table_info(scan_jobs)')}
            if 'started_at' in columns:
                job = conn.execute("SELECT started_at FROM scan_jobs WHERE task_id=?", (row['task_id'],)).fetchone()
                started_at = job[0] if job else None
        return {
            'run_id': row['run_id'], 'task_id': row['task_id'], 'as_of': row['as_of'], 'started_at': started_at,
            'completed_at': row['created_at'], 'config_hash': row['config_hash'],
            'entry_hash': publication.get('entry_hash'), 'dataset_version': row['dataset_version'],
            'result_hash': row['result_hash'], 'code_version': row['git_sha'],
            'verified': publication.get('version') == PUBLICATION_VERSION and integrity_error is None,
            'publication_version': publication.get('version', 1),
            'state': publication.get('state', 'LEGACY_UNVERIFIED'),
            'freshness': publication.get('freshness', {}), 'regime': publication.get('regime', {}),
            'counts': publication.get('counts', {}), 'pool_report': publication.get('pool_report'), 'strategy_snapshot': snapshot,
            'qualification': qualification, 'qualified_available': available, 'qualified_verified': qualified_verified,
            'qualification_integrity_error': integrity_error, 'integrity_error': integrity_error, 'stage': stage,
            'candidates': qualified if stage == 'qualified' else candidates,
        }


def publish_standalone_scan(db_path: str | Path, *, as_of: str, days: int, profile: Any,
                            candidates: list[dict], freshness: dict, regime: dict,
                            input_dataset_version: str | None = None,
                            qualified_candidates: list[dict] | None = None,
                            qualification_report: dict | None = None, started_at: str | None = None,
                            pool_report: dict | None = None) -> str:
    """CLI scans use the same commit protocol as the Web worker."""
    import uuid

    from ab_screener.application.scan_audit import complete_scan_run
    from ab_screener.application.scan_jobs import FAILED, ScanJobStore
    from build_version import build_version

    task_id = 'cli-' + uuid.uuid4().hex[:12]
    jobs = ScanJobStore(db_path)
    jobs.reserve_running(task_id, top_n=len(candidates), days=days)
    try:
        if started_at is not None:
            # CLI reservation happens after computation; preserve the actual run start.
            from datetime import datetime
            start = datetime.fromisoformat(started_at)
            if start.tzinfo is None or start > datetime.now(start.tzinfo):
                raise ValueError("invalid actual scan start")
        # Reservation happens after computation and cannot certify when a CLI
        # scan began. Unknown actual start must remain unknown to forward capture.
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("UPDATE scan_jobs SET started_at=? WHERE task_id=?", (started_at, task_id))
        result = {'scan_candidates': candidates, 'freshness': freshness, 'regime': regime, 'pool_report': pool_report,
                  'input_dataset_version': input_dataset_version, 'total_candidates': len(candidates), 'hits': len(candidates)}
        if qualified_candidates is not None or qualification_report is not None:
            result.update(qualified_candidates=qualified_candidates, qualification_report=qualification_report)
        completed = complete_scan_run(
            db_path, run_id=task_id, task_id=task_id, as_of=as_of, days=days,
            result=result,
            count_a=sum(str(row.get('reasons', '')).startswith('[池A|') for row in candidates),
            count_b=sum(str(row.get('reasons', '')).startswith('[池B|') for row in candidates),
            strategy_snapshot=profile.to_canonical_dict(), config_hash=profile.config_hash(),
            code_version=build_version(), research_mode='daily_research',
        )
        if not completed:
            raise RuntimeError('scan was cancelled before publication')
    except Exception as exc:
        jobs.finish(task_id, status=FAILED, error_code='PUBLICATION_FAILED', error_message=str(exc))
        raise
    return task_id
