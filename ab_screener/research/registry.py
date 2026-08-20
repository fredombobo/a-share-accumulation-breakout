"""实验注册与 trial 登记（P3.1）。

契约：
- 实验注册后核心字段（strategy/params/config_hash）不可修改（DB 触发器兜底）。
- 失败、取消、被拒绝的参数组合同样登记进 research_trials（不静默丢弃）。
- 表未迁移 → 抛错（fail-closed；DDL 唯一入口 migrate_v2.py）。
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.data_point import canonical_json

_TZ = ZoneInfo("Asia/Shanghai")

EXPERIMENT_STATUSES = ("REGISTERED", "RUNNING", "COMPLETED", "CANCELLED", "REJECTED")
TRIAL_STATUSES = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "REJECTED")


class ResearchGovernanceError(RuntimeError):
    """研究治理领域错误（fail-closed 信号）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _require_table(conn: sqlite3.Connection, table: str) -> None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not has:
        raise ResearchGovernanceError(
            f"{table} 表不存在：先运行 scripts/migrate_v2.py --apply（fail-closed）"
        )


def experiment_id_for(strategy: str, params: dict[str, Any], config_hash: str) -> str:
    blob = canonical_json({"strategy": strategy, "params": params, "config_hash": config_hash})
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def register_experiment(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    params: dict[str, Any],
    config_hash: str,
) -> str:
    """注册实验（幂等：同核心指纹返回既有 id）。"""
    _require_table(conn, "experiment_registrations")
    experiment_id = experiment_id_for(strategy, params, config_hash)
    existing = conn.execute(
        "SELECT 1 FROM experiment_registrations WHERE experiment_id=?", (experiment_id,)
    ).fetchone()
    if existing:
        return experiment_id
    now = _now()
    conn.execute(
        "INSERT INTO experiment_registrations (experiment_id, strategy, params_json,"
        " config_hash, status, registered_at, updated_at)"
        " VALUES (?,?,?,?, 'REGISTERED', ?, ?)",
        (experiment_id, strategy, canonical_json(params), config_hash, now, now),
    )
    conn.commit()
    return experiment_id


def transition_experiment_status(
    conn: sqlite3.Connection, experiment_id: str, status: str
) -> None:
    """状态推进（CANCELLED/REJECTED 照常登记）。"""
    if status not in EXPERIMENT_STATUSES:
        raise ResearchGovernanceError(f"非法实验状态: {status}")
    _require_table(conn, "experiment_registrations")
    cur = conn.execute(
        "UPDATE experiment_registrations SET status=?, updated_at=? WHERE experiment_id=?",
        (status, _now(), experiment_id),
    )
    if cur.rowcount == 0:
        raise ResearchGovernanceError(f"实验不存在: {experiment_id}")
    conn.commit()


def register_trial(
    conn: sqlite3.Connection,
    *,
    experiment_id: str,
    params: dict[str, Any],
    status: str,
    outcome: dict[str, Any] | None = None,
) -> str:
    """登记一次 trial；FAILED/CANCELLED/REJECTED 同登记。"""
    if status not in TRIAL_STATUSES:
        raise ResearchGovernanceError(f"非法 trial 状态: {status}")
    _require_table(conn, "research_trials")
    exp = conn.execute(
        "SELECT 1 FROM experiment_registrations WHERE experiment_id=?", (experiment_id,)
    ).fetchone()
    if exp is None:
        raise ResearchGovernanceError(f"实验未注册: {experiment_id}")
    # 同一参数可重跑多次（账本语义）；用实验内序号保证 trial_id 唯一
    seq = conn.execute(
        "SELECT COUNT(*) FROM research_trials WHERE experiment_id=?", (experiment_id,)
    ).fetchone()[0]
    blob = canonical_json(
        {"experiment_id": experiment_id, "seq": seq + 1,
         "params": params, "status": status}
    )
    trial_id = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    now = _now()
    conn.execute(
        "INSERT INTO research_trials (trial_id, experiment_id, params_json, status,"
        " outcome_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (trial_id, experiment_id, canonical_json(params), status,
         canonical_json(outcome) if outcome is not None else None, now, now),
    )
    conn.commit()
    return trial_id


def require_experiment(conn: sqlite3.Connection, experiment_id: str) -> dict[str, Any]:
    """读取实验登记；不存在 → 抛错（fail-closed）。"""
    _require_table(conn, "experiment_registrations")
    row = conn.execute(
        "SELECT experiment_id, strategy, params_json, config_hash, status"
        " FROM experiment_registrations WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()
    if row is None:
        raise ResearchGovernanceError(f"实验不存在: {experiment_id}")
    import json

    return {
        "experiment_id": row[0],
        "strategy": row[1],
        "params": json.loads(row[2]),
        "config_hash": row[3],
        "status": row[4],
    }


def list_experiments(db_path: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    from ab_screener.data.db import SchemaMissing, connect, table_exists

    with connect(db_path) as conn:
        if not table_exists(conn, "experiment_registrations"):
            raise SchemaMissing("v2:research_governance 未迁移")
        rows = conn.execute(
            "SELECT experiment_id, strategy, params_json, config_hash, status, registered_at"
            " FROM experiment_registrations ORDER BY registered_at DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
    import json

    return [
        {
            "experiment_id": r[0],
            "strategy": r[1],
            "params": json.loads(r[2]) if r[2] else {},
            "config_hash": r[3],
            "status": r[4],
            "registered_at": r[5],
        }
        for r in rows
    ]


def register_experiment_at(
    db_path: str | Path,
    *,
    strategy: str,
    params: dict[str, Any],
    config_hash: str,
) -> str:
    from ab_screener.data.db import connect

    with connect(db_path) as conn:
        return register_experiment(
            conn, strategy=strategy, params=params, config_hash=config_hash
        )


def require_experiment_at(db_path: str | Path, experiment_id: str) -> dict[str, Any]:
    from ab_screener.data.db import connect

    with connect(db_path) as conn:
        return require_experiment(conn, experiment_id)
