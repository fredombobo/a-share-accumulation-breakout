"""纸面账户只读查询（API 层禁止直连 sqlite）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def load_dashboard_extras(db_path: str | Path) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        curve_rows = conn.execute(
            "SELECT s.trade_date, s.cash_fen, s.market_value_fen, s.total_asset_fen,"
            " s.realized_pnl_fen, s.unrealized_pnl_fen, s.drawdown_fen "
            "FROM pt_daily_snapshot s JOIN pt_cycle c ON c.run_date=s.trade_date "
            "WHERE s.account_id=1 AND c.phase='DONE' "
            "ORDER BY s.trade_date DESC LIMIT 250"
        ).fetchall()
        unresolved = conn.execute(
            "SELECT COUNT(*) FROM pt_reconciliation WHERE status IN ('OPEN','ESCALATED') "
            "AND result!='OK'"
        ).fetchone()[0]
        reserves = conn.execute(
            "SELECT COALESCE(SUM(reserve_fen),0), COALESCE(SUM(reserved_qty),0) "
            "FROM pt_order WHERE account_id=1 AND state IN ('CONFIRMED','QUEUED')"
        ).fetchone()
    finally:
        conn.close()
    curve = [
        {
            "trade_date": row[0],
            "cash_fen": row[1],
            "market_value_fen": row[2],
            "total_asset_fen": row[3],
            "realized_pnl_fen": row[4],
            "unrealized_pnl_fen": row[5],
            "drawdown_fen": row[6],
        }
        for row in reversed(curve_rows)
    ]
    return {
        "equity_curve": curve,
        "unresolved_reconciliation_count": int(unresolved),
        "reserved_cash_fen": int(reserves[0]),
        "reserved_sell_qty": int(reserves[1]),
    }


def latest_gate_status(db_path: str | Path) -> dict[str, Any]:
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT passed, data_version, issues_json, generated_at, report_sha256 "
                "FROM pt_gate_report ORDER BY report_id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return {"status": "NOT_RUN", "note": "尚无真实数据门禁报告"}
        return {
            "status": "PASS" if row[0] else "FAIL",
            "data_version": row[1],
            "issues": json.loads(row[2]),
            "generated_at": row[3],
            "report_sha256": row[4],
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "note": str(exc)[:160]}


def cycle_status(db_path: str | Path, trade_date: str) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT cycle_id, run_date, phase, retry_count, data_version,"
            " blocked_reason, started_at, finished_at FROM pt_cycle WHERE run_date=?",
            (trade_date,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"trade_date": trade_date, "phase": None, "blocked_reason": "未运行"}
    return {
        "trade_date": trade_date,
        "phase": row[2],
        "cycle_id": row[0],
        "retry_count": row[3],
        "data_version": row[4],
        "blocked_reason": row[5],
        "started_at": row[6],
        "finished_at": row[7],
    }


def list_reconciliations(
    db_path: str | Path, trade_date: str | None = None
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        if trade_date:
            rows = conn.execute(
                "SELECT rec_id, run_date, result, diff_json, severity, status, checked_at"
                " FROM pt_reconciliation WHERE run_date=? ORDER BY rec_id DESC LIMIT 20",
                (trade_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT rec_id, run_date, result, diff_json, severity, status, checked_at"
                " FROM pt_reconciliation ORDER BY rec_id DESC LIMIT 20"
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "rec_id": r[0],
            "run_date": r[1],
            "result": r[2],
            "diff_json": r[3],
            "severity": r[4],
            "status": r[5],
            "checked_at": r[6],
        }
        for r in rows
    ]


def list_corporate_actions(
    db_path: str | Path, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    sql = (
        "SELECT action_id, ts_code, ex_date, kind, amount_fen, ratio, note, status,"
        " applied_at, adjustment_ref FROM pt_corporate_action WHERE 1=1"
    )
    params: list[Any] = []
    if status:
        sql += " AND status=?"
        params.append(status.upper())
    sql += " ORDER BY ex_date DESC, action_id DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "action_id": row[0],
            "ts_code": row[1],
            "ex_date": row[2],
            "kind": row[3],
            "amount_fen": row[4],
            "ratio": row[5],
            "note": row[6],
            "status": row[7],
            "applied_at": row[8],
            "adjustment_ref": row[9],
        }
        for row in rows
    ]


def list_fills(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT fill_id, order_id, ref_open_price_micro, fill_price_micro, qty,"
            " commission_fen, tax_fen, fill_model_version, quote_revision, filled_at"
            " FROM pt_fill ORDER BY filled_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "fill_id": r[0],
            "order_id": r[1],
            "ref_open_price_micro": r[2],
            "fill_price_micro": r[3],
            "qty": r[4],
            "commission_fen": r[5],
            "tax_fen": r[6],
            "fill_model_version": r[7],
            "quote_revision": r[8],
            "filled_at": r[9],
        }
        for r in rows
    ]


def last_done_cycle_date(db_path: str | Path) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT run_date FROM pt_cycle WHERE phase='DONE' ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None
