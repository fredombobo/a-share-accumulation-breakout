"""策略库/回测记录读取（只读 repository）。

读 logic_strategies + logic_backtests 表；写路径走 CLI/闸门（ON CONFLICT DO UPDATE）。
每操作新连接（AGENTS.md 硬约束）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_STATUS_LABEL = {
    "draft": "草稿（数据不足）", "research": "研究", "gated": "已过闸门",
    "rejected": "未过闸门", "archived": "归档",
}


def _connect(db_path: str | Path):
    return sqlite3.connect(str(db_path), timeout=30)


def list_strategies(db_path: str | Path) -> list[dict]:
    """策略列表（含最近回测摘要与闸门状态）。"""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT s.id, s.version, s.name, s.status, s.research_only,
                      s.metrics_json, s.updated_at,
                      (SELECT b.metrics_json FROM logic_backtests b
                        WHERE b.strategy_id = s.id ORDER BY b.created_at DESC LIMIT 1)
                      AS last_bt_metrics
               FROM logic_strategies s
               ORDER BY s.updated_at DESC"""
        ).fetchall()
    out = []
    for r in rows:
        gate = _safe_json(r[5]) or {}
        bt = _safe_json(r[6]) or {}
        out.append({
            "id": r[0], "version": r[1], "name": r[2], "status": r[3],
            "status_label": _STATUS_LABEL.get(r[3], r[3]),
            "research_only": bool(r[4]),
            "gate": gate,
            "metrics": bt,
            "updated_at": r[7],
        })
    return out


def get_strategy(db_path: str | Path, strategy_id: str) -> dict | None:
    """策略详情：DSL YAML + 全部回测记录。"""
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT id, version, name, dsl_yaml, status, research_only,
                      metrics_json, created_at, updated_at
               FROM logic_strategies WHERE id = ?""", (strategy_id,)
        ).fetchone()
        if row is None:
            return None
        bt_rows = conn.execute(
            """SELECT run_id, params_json, window_json, metrics_json, created_at
               FROM logic_backtests WHERE strategy_id = ?
               ORDER BY created_at DESC LIMIT 20""", (strategy_id,)
        ).fetchall()
    return {
        "id": row[0], "version": row[1], "name": row[2],
        "dsl_yaml": row[3], "status": row[4],
        "status_label": _STATUS_LABEL.get(row[4], row[4]),
        "research_only": bool(row[5]),
        "gate": _safe_json(row[6]) or {},
        "created_at": row[7], "updated_at": row[8],
        "backtests": [
            {
                "run_id": b[0], "params": _safe_json(b[1]),
                "window": _safe_json(b[2]), "metrics": _safe_json(b[3]),
                "created_at": b[4],
            }
            for b in bt_rows
        ],
    }


def get_backtest(db_path: str | Path, run_id: str) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT run_id, strategy_id, params_json, window_json,
                      metrics_json, equity_path, created_at
               FROM logic_backtests WHERE run_id = ?""", (run_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row[0], "strategy_id": row[1], "params": _safe_json(row[2]),
        "window": _safe_json(row[3]), "metrics": _safe_json(row[4]),
        "equity_path": row[5], "created_at": row[6],
    }


def _safe_json(text) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
