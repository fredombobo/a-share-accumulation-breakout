"""upgrade system 治理表迁移（schema_version 9+）。

仅新增表/索引，不删除旧数据。
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def migrate_v9_governance(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
    CREATE TABLE IF NOT EXISTS dataset_partitions (
        dataset TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        content_sha256 TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        ingested_at TEXT NOT NULL,
        PRIMARY KEY (dataset, trade_date)
    );
    CREATE INDEX IF NOT EXISTS idx_dp_dataset ON dataset_partitions(dataset);

    CREATE TABLE IF NOT EXISTS scan_jobs (
        task_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        top_n INTEGER,
        days INTEGER,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        worker_id TEXT,
        heartbeat_at TEXT,
        checkpoint_json TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        error_code TEXT,
        error_message TEXT,
        run_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_scan_jobs_status ON scan_jobs(status, created_at);

    CREATE TABLE IF NOT EXISTS scan_runs (
        run_id TEXT PRIMARY KEY,
        task_id TEXT,
        as_of TEXT,
        strategy_snapshot_json TEXT,
        config_hash TEXT,
        git_sha TEXT,
        dataset_version TEXT,
        random_seed INTEGER,
        input_hash TEXT,
        result_hash TEXT,
        research_mode TEXT,
        status TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS scan_run_candidates (
        run_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        stage TEXT NOT NULL,
        pool TEXT,
        tier TEXT,
        total_score REAL,
        reject_reason TEXT,
        payload_json TEXT,
        PRIMARY KEY (run_id, ts_code, stage)
    );
    CREATE INDEX IF NOT EXISTS idx_src_run ON scan_run_candidates(run_id, stage);

    CREATE TABLE IF NOT EXISTS strategy_profiles (
        profile_id TEXT NOT NULL,
        version TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        status TEXT NOT NULL,
        config_json TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (profile_id, version)
    );

    CREATE TABLE IF NOT EXISTS research_runs (
        research_run_id TEXT PRIMARY KEY,
        strategy TEXT,
        research_mode TEXT,
        can_claim_edge INTEGER NOT NULL DEFAULT 0,
        is_json TEXT,
        oos_json TEXT,
        baselines_json TEXT,
        promotion_json TEXT,
        config_hash TEXT,
        created_at TEXT NOT NULL
    );
    """
    )


def migrate_v10_daily_partition_invalidation(conn: sqlite3.Connection) -> None:
    """让 SQLite 行情主表的每次变更都使对应 Parquet 分区指纹失效。"""
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_daily_partition_invalidate_insert
        AFTER INSERT ON daily
        BEGIN
            DELETE FROM dataset_partitions
            WHERE dataset = 'daily' AND trade_date = NEW.trade_date;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_daily_partition_invalidate_update
        AFTER UPDATE ON daily
        BEGIN
            DELETE FROM dataset_partitions
            WHERE dataset = 'daily'
              AND trade_date IN (OLD.trade_date, NEW.trade_date);
        END;

        CREATE TRIGGER IF NOT EXISTS trg_daily_partition_invalidate_delete
        AFTER DELETE ON daily
        BEGIN
            DELETE FROM dataset_partitions
            WHERE dataset = 'daily' AND trade_date = OLD.trade_date;
        END;
        """
    )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migrate_v11_research_run_persistence(conn: sqlite3.Connection) -> None:
    """Persist Lab lifecycle/checkpoints and quarantine unverifiable active params."""
    # Some early v9 fixtures/databases recorded the version before every
    # governance table existed.  Re-applying the additive v9 DDL is safe and
    # makes the forward migration self-healing.
    migrate_v9_governance(conn)
    _ensure_columns(
        conn,
        "research_runs",
        {
            "status": "TEXT NOT NULL DEFAULT 'done'",
            "phase": "TEXT",
            "progress": "INTEGER NOT NULL DEFAULT 100",
            "message": "TEXT",
            "request_json": "TEXT",
            "checkpoint_json": "TEXT",
            "result_json": "TEXT",
            "verdict": "TEXT",
            "candidate_eligible": "INTEGER NOT NULL DEFAULT 0",
            "dataset_version": "TEXT",
            "code_version": "TEXT",
            "cost_version": "TEXT",
            "input_hash": "TEXT",
            "report_markdown": "TEXT",
            "report_sha256": "TEXT",
            "started_at": "TEXT",
            "finished_at": "TEXT",
            "updated_at": "TEXT",
        },
    )
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_research_runs_status_updated
          ON research_runs(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_research_runs_input_hash
          ON research_runs(input_hash, status, finished_at);

        CREATE TABLE IF NOT EXISTS research_candidates (
            candidate_id TEXT PRIMARY KEY,
            research_run_id TEXT NOT NULL REFERENCES research_runs(research_run_id),
            param_id TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'isolated'
              CHECK (status IN ('isolated','reviewed','rejected')),
            params_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(research_run_id, param_id)
        );
        CREATE INDEX IF NOT EXISTS idx_research_candidates_run
          ON research_candidates(research_run_id, status);
        """
    )
    # Pre-v11 active rows have no trusted report/candidate provenance.  Preserve
    # them for audit while making them invisible to active_weights().
    strategy_params_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_params'"
    ).fetchone()
    if strategy_params_exists:
        conn.execute(
            "UPDATE strategy_params SET status='legacy_unverified' WHERE status='active'"
        )


def migrate_v12_single_active_research_run(conn: sqlite3.Connection) -> None:
    """Make the persisted Lab lifecycle authoritative and single-active."""
    migrate_v11_research_run_persistence(conn)
    _ensure_columns(
        conn,
        "research_runs",
        {
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "worker_id": "TEXT",
            "heartbeat_at": "TEXT",
        },
    )
    active = conn.execute(
        "SELECT research_run_id FROM research_runs "
        "WHERE status IN ('pending','running','cancelling') "
        "ORDER BY COALESCE(updated_at,created_at) DESC"
    ).fetchall()
    for row in active[1:]:
        conn.execute(
            "UPDATE research_runs SET status='interrupted', "
            "message='迁移时发现并发活动任务；已保留最新任务并隔离本记录', "
            "updated_at=? WHERE research_run_id=?",
            (_now_iso(), row[0]),
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_research_runs_one_active "
        "ON research_runs((1)) WHERE status IN ('pending','running','cancelling')"
    )


def migrate_v13_daily_run_manifest(conn: sqlite3.Connection) -> None:
    """Add immutable cross-domain evidence for each completed trading day."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS daily_run_manifests (
            manifest_id TEXT PRIMARY KEY,
            trade_date TEXT NOT NULL,
            account_id INTEGER,
            status TEXT NOT NULL CHECK (status IN ('COMPLETE','PARTIAL')),
            data_version TEXT,
            code_version TEXT,
            config_hash TEXT,
            scan_run_id TEXT,
            paper_cycle_id TEXT,
            payload_json TEXT NOT NULL,
            blockers_json TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_daily_manifest_date
          ON daily_run_manifests(trade_date, created_at);
        CREATE TRIGGER IF NOT EXISTS trg_daily_manifest_no_update
        BEFORE UPDATE ON daily_run_manifests
        BEGIN
            SELECT RAISE(ABORT, 'daily_run_manifests is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_daily_manifest_no_delete
        BEFORE DELETE ON daily_run_manifests
        BEGIN
            SELECT RAISE(ABORT, 'daily_run_manifests is append-only');
        END;
        """
    )


def run_v2_migrations(db_path: str | Path, *, verbose: bool = False) -> int:
    """执行 upgrade system 增量迁移；返回当前 max version。"""
    path = Path(db_path)
    conn = sqlite3.connect(str(path), timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
              version   INTEGER PRIMARY KEY,
              name      TEXT NOT NULL,
              checksum  TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            int(row[0]) for row in conn.execute("SELECT version FROM schema_version").fetchall()
        }
        if 9 not in applied:
            src = "migrate_v9_governance"
            checksum = hashlib.sha1(src.encode()).hexdigest()
            conn.execute("BEGIN IMMEDIATE")
            try:
                migrate_v9_governance(conn)
                conn.execute(
                    "INSERT INTO schema_version(version, name, checksum, applied_at) VALUES (?,?,?,?)",
                    (9, "M009_upgrade_governance", checksum, _now_iso()),
                )
                conn.commit()
                if verbose:
                    print("[migrate] applied v9 governance tables")
            except Exception:
                conn.rollback()
                raise
            applied.add(9)

        daily_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily'"
        ).fetchone()
        if 10 not in applied and daily_exists:
            src = "migrate_v10_daily_partition_invalidation"
            checksum = hashlib.sha1(src.encode()).hexdigest()
            conn.execute("BEGIN IMMEDIATE")
            try:
                migrate_v10_daily_partition_invalidation(conn)
                conn.execute(
                    "INSERT INTO schema_version(version, name, checksum, applied_at) VALUES (?,?,?,?)",
                    (10, "M010_daily_partition_invalidation", checksum, _now_iso()),
                )
                conn.commit()
                if verbose:
                    print("[migrate] applied v10 daily partition invalidation")
            except Exception:
                conn.rollback()
                raise
            applied.add(10)

        if 11 not in applied:
            src = "migrate_v11_research_run_persistence"
            checksum = hashlib.sha1(src.encode()).hexdigest()
            conn.execute("BEGIN IMMEDIATE")
            try:
                migrate_v11_research_run_persistence(conn)
                conn.execute(
                    "INSERT INTO schema_version(version, name, checksum, applied_at) VALUES (?,?,?,?)",
                    (11, "M011_research_run_persistence", checksum, _now_iso()),
                )
                conn.commit()
                if verbose:
                    print("[migrate] applied v11 research run persistence")
            except Exception:
                conn.rollback()
                raise
            applied.add(11)

        if 12 not in applied:
            src = "migrate_v12_single_active_research_run"
            checksum = hashlib.sha1(src.encode()).hexdigest()
            conn.execute("BEGIN IMMEDIATE")
            try:
                migrate_v12_single_active_research_run(conn)
                conn.execute(
                    "INSERT INTO schema_version(version, name, checksum, applied_at) VALUES (?,?,?,?)",
                    (12, "M012_single_active_research_run", checksum, _now_iso()),
                )
                conn.commit()
                if verbose:
                    print("[migrate] applied v12 single active research run")
            except Exception:
                conn.rollback()
                raise
            applied.add(12)

        if 13 not in applied:
            src = "migrate_v13_daily_run_manifest"
            checksum = hashlib.sha1(src.encode()).hexdigest()
            conn.execute("BEGIN IMMEDIATE")
            try:
                migrate_v13_daily_run_manifest(conn)
                conn.execute(
                    "INSERT INTO schema_version(version, name, checksum, applied_at) VALUES (?,?,?,?)",
                    (13, "M013_daily_run_manifest", checksum, _now_iso()),
                )
                conn.commit()
                if verbose:
                    print("[migrate] applied v13 daily run manifest")
            except Exception:
                conn.rollback()
                raise
            applied.add(13)
        cur = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
        return int(cur[0] or 0)
    finally:
        conn.close()
