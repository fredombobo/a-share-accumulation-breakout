"""风险快照仓库（P5.2）：append-only 快照（行情/规则/配置版本 + 指标）。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

RISK_RULE_VERSION = "risk-v2"
RISK_CONFIG_VERSION = "robust_personal_v2"


class RiskRepositoryError(RuntimeError):
    """风险仓库错误（fail-closed）。"""


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def save_risk_snapshot(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    market_version: str,
    metrics: dict[str, Any],
    scenarios: dict[str, Any],
) -> str:
    """保存不可变风险快照（append-only；同内容幂等跳过）。"""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='risk_snapshots'"
    ).fetchone()
    if not has:
        raise RiskRepositoryError(
            "risk_snapshots 表不存在：先运行 scripts/migrate_v2.py --apply（fail-closed）"
        )
    snapshot_id = hashlib.sha256(
        json.dumps({"date": trade_date, "market": market_version, "metrics": metrics},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    existing = conn.execute(
        "SELECT 1 FROM risk_snapshots WHERE snapshot_id=?", (snapshot_id,)
    ).fetchone()
    if existing:
        return snapshot_id
    now = _now()
    conn.execute(
        "INSERT INTO risk_snapshots (snapshot_id, trade_date, account_id, market_version,"
        " rule_version, config_version, metrics_json, scenarios_json, created_at)"
        " VALUES (?,?,1,?,?,?,?,?,?)",
        (snapshot_id, trade_date, market_version, RISK_RULE_VERSION, RISK_CONFIG_VERSION,
         json.dumps(metrics, ensure_ascii=False, sort_keys=True, default=str),
         json.dumps(scenarios, ensure_ascii=False, sort_keys=True, default=str), now),
    )
    conn.commit()
    return snapshot_id


def latest_risk_snapshot(
    conn: sqlite3.Connection, trade_date: str | None = None
) -> dict[str, Any] | None:
    sql = (
        "SELECT snapshot_id, trade_date, market_version, rule_version, config_version,"
        " metrics_json, scenarios_json, created_at FROM risk_snapshots"
    )
    params: list[Any] = []
    if trade_date:
        sql += " WHERE trade_date=?"
        params.append(trade_date)
    row = conn.execute(sql + " ORDER BY created_at DESC LIMIT 1", params).fetchone()
    if row is None:
        return None
    return {
        "snapshot_id": row[0], "trade_date": row[1], "market_version": row[2],
        "rule_version": row[3], "config_version": row[4],
        "metrics": json.loads(row[5]), "scenarios": json.loads(row[6]),
        "created_at": row[7],
    }
