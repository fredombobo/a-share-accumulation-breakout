"""Deterministic append-only evidence linking one complete daily workflow."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ab_screener.data.migrations_v2 import run_v2_migrations

_TZ = ZoneInfo("Asia/Shanghai")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    result["blockers"] = json.loads(result.pop("blockers_json"))
    return result


def _latest_manifest(conn: sqlite3.Connection, trade_date: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM daily_run_manifests WHERE trade_date=? "
        "ORDER BY created_at DESC, manifest_id DESC LIMIT 1",
        (trade_date,),
    ).fetchone()


def create_daily_manifest(db_path: str | Path, trade_date: str) -> dict[str, Any]:
    """Snapshot hashes and references across data, scan, signal, paper and reconciliation."""
    db_path = Path(db_path)
    trade_date = "".join(ch for ch in str(trade_date) if ch.isdigit())[:8]
    if len(trade_date) != 8:
        raise ValueError("trade_date must be YYYYMMDD")
    run_v2_migrations(db_path)
    with sqlite3.connect(str(db_path), timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        scan = conn.execute(
            "SELECT run_id,task_id,as_of,config_hash,git_sha,dataset_version,input_hash,"
            "result_hash,research_mode,status,created_at FROM scan_runs "
            "WHERE as_of=? AND status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
        signals = _dicts(conn.execute(
            "SELECT trade_date,ts_code,pool,total_score,suggested_pos_pct,strategy_version,"
            "input_hash,available_at,source,revision,tradeable FROM pt_signal_snapshot "
            "WHERE trade_date=? ORDER BY pool,ts_code",
            (trade_date,),
        ).fetchall())
        orders = _dicts(conn.execute(
            "SELECT order_id,source,ts_code,side,qty,state,reserve_fen,reserved_qty,"
            "signal_trade_date,eligible_trade_date,reject_reason FROM pt_order "
            "WHERE signal_trade_date=? OR eligible_trade_date=? ORDER BY order_id",
            (trade_date, trade_date),
        ).fetchall())
        fills = _dicts(conn.execute(
            "SELECT f.fill_id,f.order_id,f.ref_open_price_micro,f.fill_price_micro,f.qty,"
            "f.commission_fen,f.tax_fen,f.fill_model_version,f.quote_revision "
            "FROM pt_fill f JOIN pt_order o ON o.order_id=f.order_id "
            "WHERE o.eligible_trade_date=? OR f.quote_revision LIKE ? ORDER BY f.fill_id",
            (trade_date, f"%:{trade_date}"),
        ).fetchall())
        cycle = conn.execute(
            "SELECT cycle_id,run_date,phase,retry_count,data_version,blocked_reason,"
            "started_at,finished_at FROM pt_cycle WHERE run_date=? "
            "ORDER BY started_at DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
        reconciliation = conn.execute(
            "SELECT rec_id,run_date,result,diff_json,severity,status,checked_at "
            "FROM pt_reconciliation WHERE run_date=? ORDER BY rec_id DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
        snapshot = conn.execute(
            "SELECT account_id,trade_date,cash_fen,market_value_fen,total_asset_fen,"
            "realized_pnl_fen,unrealized_pnl_fen,drawdown_fen,positions_json "
            "FROM pt_daily_snapshot WHERE account_id=1 AND trade_date=?",
            (trade_date,),
        ).fetchone()
        has_risk_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='risk_snapshots'"
        ).fetchone()
        risk_snapshot = (
            conn.execute(
                "SELECT snapshot_id,trade_date,account_id,market_version,rule_version,"
                "config_version,metrics_json,scenarios_json,created_at FROM risk_snapshots"
                " WHERE account_id=1 AND trade_date=?"
                " ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (trade_date,),
            ).fetchone()
            if has_risk_table
            else None
        )

        blockers: list[str] = []
        if scan is None:
            blockers.append("MISSING_SCAN_RUN")
        if cycle is None:
            blockers.append("MISSING_PAPER_CYCLE")
        elif str(cycle["phase"]) != "DONE":
            blockers.append("PAPER_CYCLE_NOT_DONE")
        if reconciliation is None:
            blockers.append("MISSING_RECONCILIATION")
        elif str(reconciliation["result"]) != "OK":
            blockers.append("RECONCILIATION_NOT_OK")
        if snapshot is None:
            blockers.append("MISSING_DAILY_SNAPSHOT")
        if risk_snapshot is None:
            blockers.append("MISSING_RISK_SNAPSHOT")
        if scan is not None and not scan["git_sha"]:
            blockers.append("MISSING_CODE_VERSION")
        if scan is not None and not scan["config_hash"]:
            blockers.append("MISSING_CONFIG_HASH")

        scan_payload = dict(scan) if scan is not None else None
        cycle_payload = dict(cycle) if cycle is not None else None
        rec_payload = dict(reconciliation) if reconciliation is not None else None
        snapshot_payload = dict(snapshot) if snapshot is not None else None
        risk_payload = dict(risk_snapshot) if risk_snapshot is not None else None
        status = "COMPLETE" if not blockers else "PARTIAL"
        payload: dict[str, Any] = {
            "schema": "daily-run-manifest-v1",
            "trade_date": trade_date,
            "status": status,
            "blockers": blockers,
            "versions": {
                "data": scan_payload.get("dataset_version") if scan_payload else (
                    cycle_payload.get("data_version") if cycle_payload else None
                ),
                "code": scan_payload.get("git_sha") if scan_payload else None,
                "config": scan_payload.get("config_hash") if scan_payload else None,
            },
            "scan": ({**scan_payload, "candidate_rows_sha256": _sha(_dicts(conn.execute(
                "SELECT ts_code,stage,pool,tier,total_score,reject_reason,payload_json "
                "FROM scan_run_candidates WHERE run_id=? ORDER BY stage,ts_code",
                (scan_payload["run_id"],),
            ).fetchall()))} if scan_payload else None),
            "signals": {"count": len(signals), "rows_sha256": _sha(signals)},
            "orders": {"count": len(orders), "rows_sha256": _sha(orders)},
            "fills": {"count": len(fills), "rows_sha256": _sha(fills)},
            "paper": cycle_payload,
            "reconciliation": rec_payload,
            "snapshot": ({"account_id": snapshot_payload["account_id"],
                           "rows_sha256": _sha(snapshot_payload)} if snapshot_payload else None),
            "risk_snapshot": (
                {
                    "snapshot_id": risk_payload["snapshot_id"],
                    "market_version": risk_payload["market_version"],
                    "rule_version": risk_payload["rule_version"],
                    "config_version": risk_payload["config_version"],
                    "rows_sha256": _sha(risk_payload),
                }
                if risk_payload else None
            ),
        }
        digest = _sha(payload)
        manifest_id = f"DM-{trade_date}-{digest[:16]}"
        created_at = datetime.now(_TZ).isoformat(timespec="seconds")
        conn.execute(
            "INSERT OR IGNORE INTO daily_run_manifests("
            "manifest_id,trade_date,account_id,status,data_version,code_version,config_hash,"
            "scan_run_id,paper_cycle_id,payload_json,blockers_json,manifest_sha256,created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                manifest_id, trade_date, 1 if snapshot_payload else None, status,
                payload["versions"]["data"], payload["versions"]["code"],
                payload["versions"]["config"],
                scan_payload.get("run_id") if scan_payload else None,
                cycle_payload.get("cycle_id") if cycle_payload else None,
                _canonical(payload), _canonical(blockers), digest, created_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM daily_run_manifests WHERE manifest_sha256=?", (digest,)
        ).fetchone()
        conn.commit()
    decoded = _decode(row)
    assert decoded is not None
    return decoded


def get_daily_manifest(db_path: str | Path, trade_date: str) -> dict[str, Any] | None:
    run_v2_migrations(db_path)
    with sqlite3.connect(str(db_path), timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        row = _latest_manifest(conn, "".join(ch for ch in str(trade_date) if ch.isdigit())[:8])
    return _decode(row)


def list_daily_manifests(db_path: str | Path, limit: int = 30) -> list[dict[str, Any]]:
    run_v2_migrations(db_path)
    with sqlite3.connect(str(db_path), timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM daily_run_manifests ORDER BY trade_date DESC,created_at DESC LIMIT ?",
            (max(1, min(int(limit), 365)),),
        ).fetchall()
    return [decoded for row in rows if (decoded := _decode(row)) is not None]
