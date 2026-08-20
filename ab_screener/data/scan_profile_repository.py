"""ScanProfile 仓库（P4.2）：版本化存取、活跃 profile、run manifest。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.data_point import canonical_json
from ab_screener.domain.scan_profile import ScanProfile

_TZ = ZoneInfo("Asia/Shanghai")


class ScanProfileError(RuntimeError):
    """profile 仓库错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _require(conn: sqlite3.Connection, table: str) -> None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not has:
        raise ScanProfileError(
            f"{table} 表不存在：先运行 scripts/migrate_v2.py --apply（fail-closed）"
        )


def save_profile(conn: sqlite3.Connection, profile: ScanProfile) -> str:
    """保存 profile（版本化 upsert：同 profile_id+version 覆盖白名单字段）。"""
    _require(conn, "scan_profiles")
    conn.execute(
        "INSERT INTO scan_profiles (profile_id, version, name, strategy_ids_json,"
        " configs_json, config_hash, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(profile_id, version) DO UPDATE SET name=excluded.name,"
        " strategy_ids_json=excluded.strategy_ids_json, configs_json=excluded.configs_json,"
        " config_hash=excluded.config_hash, status=excluded.status",
        (profile.profile_id, profile.version, profile.name,
         json.dumps(list(profile.strategy_ids), ensure_ascii=False),
         json.dumps(profile.configs, ensure_ascii=False, sort_keys=True),
         profile.config_hash, profile.status, _now()),
    )
    conn.commit()
    return profile.profile_id


def get_profile(
    conn: sqlite3.Connection, profile_id: str, version: str | None = None
) -> ScanProfile | None:
    _require(conn, "scan_profiles")
    if version is None:
        row = conn.execute(
            "SELECT name, version, strategy_ids_json, configs_json, config_hash, status"
            " FROM scan_profiles WHERE profile_id=? ORDER BY version DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT name, version, strategy_ids_json, configs_json, config_hash, status"
            " FROM scan_profiles WHERE profile_id=? AND version=?",
            (profile_id, version),
        ).fetchone()
    if row is None:
        return None
    return ScanProfile(
        name=row[0], version=row[1],
        strategy_ids=tuple(json.loads(row[2])),
        configs=json.loads(row[3]),
        config_hash=row[4], status=row[5],
    )


def active_profiles(conn: sqlite3.Connection) -> list[ScanProfile]:
    _require(conn, "scan_profiles")
    rows = conn.execute(
        "SELECT name, version, strategy_ids_json, configs_json, config_hash, status"
        " FROM scan_profiles WHERE status='ACTIVE' ORDER BY created_at"
    ).fetchall()
    return [
        ScanProfile(name=r[0], version=r[1], strategy_ids=tuple(json.loads(r[2])),
                    configs=json.loads(r[3]), config_hash=r[4], status=r[5])
        for r in rows
    ]


def list_active_profiles_at(db_path: str | Path) -> list[ScanProfile]:
    from ab_screener.data.db import connect

    with connect(db_path) as conn:
        return active_profiles(conn)


def get_profile_at(
    db_path: str | Path, profile_id: str, version: str | None = None
) -> ScanProfile | None:
    from ab_screener.data.db import connect

    with connect(db_path) as conn:
        return get_profile(conn, profile_id, version)


def save_profile_at(db_path: str | Path, profile: ScanProfile) -> str:
    from ab_screener.data.db import connect

    with connect(db_path) as conn:
        return save_profile(conn, profile)


def record_funnel_run(
    conn: sqlite3.Connection,
    *,
    profile: ScanProfile,
    input_hash: str,
    stages: list[str],
    result: dict[str, Any],
    status: str = "COMPLETED",
) -> str:
    """不可变 run manifest（append-only）。"""
    _require(conn, "scan_funnel_runs")
    result_hash = hashlib.sha256(
        canonical_json(result).encode("utf-8")
    ).hexdigest()[:16]
    manifest_id = hashlib.sha256(
        canonical_json({
            "profile": profile.profile_id, "version": profile.version,
            "input": input_hash, "stages": stages, "result": result_hash,
        }).encode("utf-8")
    ).hexdigest()[:16]
    conn.execute(
        "INSERT INTO scan_funnel_runs (run_manifest_id, profile_id, profile_version,"
        " input_hash, stages_json, result_hash, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (manifest_id, profile.profile_id, profile.version, input_hash,
         json.dumps(stages, ensure_ascii=False), result_hash, status, _now()),
    )
    conn.commit()
    return manifest_id
