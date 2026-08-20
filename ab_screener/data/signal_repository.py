"""信号仓库（P4.3）：观察落库（幂等）、事件追加、投影推进、outcome 读取。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.domain.signal_lifecycle import (
    SignalLifecycleError,
    transition,
)
from ab_screener.strategies.contracts import SignalObservation

_TZ = ZoneInfo("Asia/Shanghai")


class SignalRepositoryError(RuntimeError):
    """信号仓库错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _require(conn: sqlite3.Connection, table: str) -> None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not has:
        raise SignalRepositoryError(
            f"{table} 表不存在：先运行 scripts/migrate_v2.py --apply（fail-closed）"
        )


def save_observation(conn: sqlite3.Connection, obs: SignalObservation) -> str:
    """落库不可变观察（幂等：同 observation_id 返回既有）。"""
    _require(conn, "signal_observations")
    existing = conn.execute(
        "SELECT 1 FROM signal_observations WHERE observation_id=?", (obs.observation_id,)
    ).fetchone()
    if existing:
        return obs.observation_id
    now = _now()
    conn.execute(
        "INSERT INTO signal_observations (observation_id, strategy_definition_id,"
        " strategy_hash, input_hash, snapshot_id, ts_code, signal_date, config_hash,"
        " payload_json, explanation, tradeable, entry_definition_id, observed_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (obs.observation_id, obs.strategy_definition_id, obs.strategy_hash,
         obs.input_hash, obs.snapshot_id, obs.ts_code, obs.signal_date,
         obs.config_hash, _dumps(obs.payload), obs.explanation,
         1 if obs.tradeable else 0, obs.entry_definition_id, now),
    )
    conn.execute(
        "INSERT INTO signal_lifecycle_projection (observation_id, status, updated_at,"
        " manual_exercise) VALUES (?, 'OBSERVED', ?, 0)",
        (obs.observation_id, now),
    )
    conn.commit()
    return obs.observation_id


def append_event(
    conn: sqlite3.Connection,
    *,
    observation_id: str,
    event_type: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    manual_exercise: bool = False,
) -> str:
    """追加事件并推进投影（非法转移抛错）。"""
    _require(conn, "signal_events")
    row = conn.execute(
        "SELECT status FROM signal_lifecycle_projection WHERE observation_id=?",
        (observation_id,),
    ).fetchone()
    if row is None:
        raise SignalLifecycleError(f"观察不存在: {observation_id}")
    from_state = row[0]
    to_state = event_type if event_type in (
        "QUALIFIED", "WATCHING", "TRADEABLE", "ORDER_CREATED", "ENTERED", "RETIRED"
    ) else from_state
    transition(from_state, to_state)
    if event_type == "ENTERED" and from_state != "ORDER_CREATED":
        raise SignalLifecycleError("ENTERED 只能由实际 fill 触发（ORDER_CREATED → ENTERED）")
    if manual_exercise and event_type not in ("QUALIFIED", "WATCHING", "TRADEABLE"):
        raise SignalLifecycleError("人工练习单只能推进到 WATCHING/TRADEABLE 类状态")
    seq = conn.execute(
        "SELECT COUNT(*) FROM signal_events WHERE observation_id=?", (observation_id,)
    ).fetchone()[0]
    event_id = f"EV-{observation_id}-{seq + 1}"
    now = _now()
    conn.execute(
        "INSERT INTO signal_events (event_id, observation_id, event_type, actor,"
        " payload_json, occurred_at) VALUES (?,?,?,?,?,?)",
        (event_id, observation_id, event_type, actor, _dumps(payload or {}), now),
    )
    conn.execute(
        "UPDATE signal_lifecycle_projection SET status=?, updated_at=?,"
        " manual_exercise=manual_exercise OR ? WHERE observation_id=?",
        (to_state, now, 1 if manual_exercise else 0, observation_id),
    )
    conn.commit()
    return event_id


def projection_status(conn: sqlite3.Connection, observation_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT observation_id, status, updated_at, manual_exercise, order_id"
        " FROM signal_lifecycle_projection WHERE observation_id=?",
        (observation_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "observation_id": row[0], "status": row[1], "updated_at": row[2],
        "manual_exercise": bool(row[3]), "order_id": row[4],
    }


def list_observations_at(
    db_path: str | Path,
    *,
    strategy: str | None = None,
    status: str | None = None,
    trade_date: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    from ab_screener.data.db import SchemaMissing, connect, table_exists

    sql = (
        "SELECT o.observation_id, o.strategy_definition_id, o.ts_code, o.signal_date,"
        " o.tradeable, o.entry_definition_id, p.status"
        " FROM signal_observations o"
        " LEFT JOIN signal_lifecycle_projection p ON p.observation_id = o.observation_id"
    )
    conds: list[str] = []
    params: list[Any] = []
    if strategy:
        conds.append("o.strategy_definition_id=?")
        params.append(strategy)
    if status:
        conds.append("p.status=?")
        params.append(status)
    if trade_date:
        conds.append("o.signal_date=?")
        params.append(trade_date)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY o.signal_date DESC, o.observed_at DESC LIMIT ?"
    params.append(limit)
    with connect(db_path, readonly=True) as conn:
        if not table_exists(conn, "signal_observations"):
            raise SchemaMissing("信号表未迁移")
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "observation_id": r[0],
            "strategy_definition_id": r[1],
            "ts_code": r[2],
            "signal_date": r[3],
            "tradeable": bool(r[4]),
            "entry_definition_id": r[5],
            "status": r[6] or "OBSERVED",
        }
        for r in rows
    ]


def get_observation_at(db_path: str | Path, observation_id: str) -> dict[str, Any] | None:
    import json

    from ab_screener.data.db import connect

    with connect(db_path, readonly=True) as conn:
        row = conn.execute(
            "SELECT observation_id, strategy_definition_id, strategy_hash, input_hash,"
            " snapshot_id, ts_code, signal_date, config_hash, payload_json, explanation,"
            " tradeable, entry_definition_id, observed_at"
            " FROM signal_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "observation_id": row[0],
        "strategy_definition_id": row[1],
        "strategy_hash": row[2],
        "input_hash": row[3],
        "snapshot_id": row[4],
        "ts_code": row[5],
        "signal_date": row[6],
        "config_hash": row[7],
        "payload": json.loads(row[8]),
        "explanation": row[9],
        "tradeable": bool(row[10]),
        "entry_definition_id": row[11],
        "observed_at": row[12],
    }


def outcomes_at(db_path: str | Path, observation_id: str) -> list[dict[str, Any]]:
    from ab_screener.application.signal_outcomes import outcomes_for_observation
    from ab_screener.data.db import connect

    with connect(db_path, readonly=True) as conn:
        return outcomes_for_observation(conn, observation_id)


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
