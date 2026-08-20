"""G1：纸面只读查询（API 不再直连 sqlite）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ab_screener.data.paper_query import (
    cycle_status,
    last_done_cycle_date,
    latest_gate_status,
    list_corporate_actions,
    list_fills,
    list_reconciliations,
    load_dashboard_extras,
)


def _init(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE pt_daily_snapshot (
            account_id INTEGER, trade_date TEXT, cash_fen INTEGER,
            market_value_fen INTEGER, total_asset_fen INTEGER,
            realized_pnl_fen INTEGER, unrealized_pnl_fen INTEGER, drawdown_fen INTEGER
        );
        CREATE TABLE pt_cycle (
            cycle_id INTEGER, run_date TEXT, phase TEXT, retry_count INTEGER,
            data_version TEXT, blocked_reason TEXT, started_at TEXT, finished_at TEXT
        );
        CREATE TABLE pt_reconciliation (
            rec_id INTEGER, run_date TEXT, result TEXT, diff_json TEXT,
            severity TEXT, status TEXT, checked_at TEXT
        );
        CREATE TABLE pt_order (
            account_id INTEGER, state TEXT, reserve_fen INTEGER, reserved_qty INTEGER
        );
        CREATE TABLE pt_gate_report (
            report_id INTEGER, passed INTEGER, data_version TEXT,
            issues_json TEXT, generated_at TEXT, report_sha256 TEXT
        );
        CREATE TABLE pt_corporate_action (
            action_id INTEGER, ts_code TEXT, ex_date TEXT, kind TEXT,
            amount_fen INTEGER, ratio REAL, note TEXT, status TEXT,
            applied_at TEXT, adjustment_ref TEXT
        );
        CREATE TABLE pt_fill (
            fill_id INTEGER, order_id TEXT, ref_open_price_micro INTEGER,
            fill_price_micro INTEGER, qty INTEGER, commission_fen INTEGER,
            tax_fen INTEGER, fill_model_version TEXT, quote_revision INTEGER,
            filled_at TEXT
        );
        INSERT INTO pt_cycle VALUES (1,'20260819','DONE',0,'v','','t0','t1');
        INSERT INTO pt_daily_snapshot VALUES (1,'20260819',100,200,300,1,2,3);
        INSERT INTO pt_reconciliation VALUES (1,'20260819','OK','[]','LOW','CLOSED','t');
        INSERT INTO pt_order VALUES (1,'CONFIRMED',50,100);
        INSERT INTO pt_gate_report VALUES (1,1,'dv','[]','t','abc');
        INSERT INTO pt_corporate_action VALUES (1,'000001.SZ','20260819','DIV',1,0,'n','OPEN','','');
        INSERT INTO pt_fill VALUES (1,'o1',1,2,100,1,0,'v1',0,'t');
        """
    )
    conn.commit()
    conn.close()


def test_paper_query_roundtrip(tmp_path: Path):
    db = tmp_path / "pt.db"
    _init(db)

    extras = load_dashboard_extras(db)
    assert extras["equity_curve"][0]["trade_date"] == "20260819"
    assert extras["reserved_cash_fen"] == 50
    assert extras["unresolved_reconciliation_count"] == 0

    gate = latest_gate_status(db)
    assert gate["status"] == "PASS"

    cyc = cycle_status(db, "20260819")
    assert cyc["phase"] == "DONE"
    missing = cycle_status(db, "19990101")
    assert missing["phase"] is None

    recs = list_reconciliations(db, "20260819")
    assert recs[0]["result"] == "OK"
    assert list_corporate_actions(db)[0]["ts_code"] == "000001.SZ"
    assert list_fills(db)[0]["order_id"] == "o1"
    assert last_done_cycle_date(db) == "20260819"


def test_gate_status_not_run(tmp_path: Path):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    assert latest_gate_status(db)["status"] in {"NOT_RUN", "ERROR"}
