"""Acceptance regressions for point-in-time, reservations and settlement blocking."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from local_store import LocalStore
from paper_trading.account import commit_import, create_account
from paper_trading.engine import execute_fills
from paper_trading.errors import DomainError
from paper_trading.orders import confirm_order, create_buy_draft, create_sell_draft, get_order
from paper_trading.settlement import apply_corporate_action, run_settlement


def _setup_db(tmp_path: Path) -> Path:
    db = tmp_path / "stock_data.db"
    LocalStore(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily "
            "(ts_code, trade_date, open, high, low, close, vol, amount) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                ("000001.SZ", "20260805", 10.0, 10.2, 9.8, 10.0, 100_000.0, 1_000_000.0),
                ("000001.SZ", "20260806", 10.0, 10.3, 9.9, 10.2, 120_000.0, 1_220_000.0),
                ("000001.SZ", "20260807", 10.2, 10.5, 10.1, 10.4, 150_000.0, 1_550_000.0),
                ("000002.SZ", "20260806", 20.0, 20.5, 19.8, 20.2, 80_000.0, 1_600_000.0),
                ("000002.SZ", "20260807", 20.2, 20.8, 20.0, 20.6, 90_000.0, 1_850_000.0),
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (cal_date, is_open, source, updated_at) "
            "VALUES (?,?,?,?)",
            [
                ("20260805", 1, "tushare", "t"),
                ("20260806", 1, "tushare", "t"),
                ("20260807", 1, "tushare", "t"),
                ("20260808", 0, "tushare", "t"),
                ("20260809", 0, "tushare", "t"),
                ("20260810", 1, "tushare", "t"),
            ],
        )
    create_account(db, 50_000_000)
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "ts_code": "000001.SZ",
                        "cost": 10.0,
                        "shares": 200,
                        "opened_at": "2026-08-01T10:00:00+08:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    commit_import(db, portfolio, as_of_date="20260806")
    return db


def _add_signal(db: Path, ts_code: str = "000002.SZ") -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pt_signal_snapshot "
            "(trade_date, ts_code, pool, total_score, suggested_pos_pct, strategy_version, "
            "input_hash, available_at) VALUES "
            "('20260806', ?, 'A', 80, 10, 'acceptance-v1', 'signal-hash', "
            "'2026-08-06T15:30:00+08:00')",
            (ts_code,),
        )


def test_confirmed_order_cannot_fill_on_signal_day(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    _add_signal(db)
    draft = create_buy_draft(
        db,
        ts_code="000002.SZ",
        trade_date="20260806",
        suggested_pos_pct=10,
        input_hash="signal-hash",
        qty=100,
        today="20260806",
    )
    confirmed = confirm_order(db, draft["order_id"], today="20260806")

    result = execute_fills(db, "20260806", today="20260806")

    assert result["filled"] == []
    assert confirmed["eligible_trade_date"] == "20260807"
    assert get_order(db, draft["order_id"])["state"] == "CONFIRMED"


def test_confirming_second_sell_respects_existing_reservation(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    first = create_sell_draft(db, ts_code="000001.SZ", qty=200, today="20260806")
    second = create_sell_draft(db, ts_code="000001.SZ", qty=200, today="20260806")
    confirm_order(db, first["order_id"], today="20260806")

    with pytest.raises(DomainError) as exc_info:
        confirm_order(db, second["order_id"], today="20260806")

    assert exc_info.value.code == "INSUFFICIENT_SELLABLE_QUANTITY"
    assert get_order(db, first["order_id"])["reserved_qty"] == 200


def test_reconciliation_diff_does_not_publish_snapshot_or_done_cycle(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO pt_cash_flow "
            "(account_id, kind, amount_fen, balance_fen, ref_id, occurred_at) "
            "VALUES (1, 'MANUAL', 1, 50000000, 'forced-diff', "
            "'2026-08-07T00:00:00+08:00')"
        )

    result = run_settlement(db, "20260807", today="20260807")

    assert result["reconciliation"]["result"] == "DIFF"
    assert result["snapshot_ok"] is False
    with sqlite3.connect(db) as conn:
        snapshot = conn.execute(
            "SELECT 1 FROM pt_daily_snapshot WHERE account_id=1 AND trade_date='20260807'"
        ).fetchone()
        phase = conn.execute(
            "SELECT phase FROM pt_cycle WHERE run_date='20260807'"
        ).fetchone()
    assert snapshot is None
    assert phase == ("RECONCILE",)


def test_pending_corporate_action_blocks_settlement_before_mutation(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO pt_corporate_action "
            "(ts_code, ex_date, kind, amount_fen, note) "
            "VALUES ('000001.SZ', '20260807', 'DIVIDEND', 1000, 'acceptance')"
        )
        action_id = conn.execute(
            "SELECT action_id FROM pt_corporate_action WHERE ts_code='000001.SZ'"
        ).fetchone()[0]
        before = conn.execute("SELECT COUNT(*) FROM pt_daily_snapshot").fetchone()[0]

    with pytest.raises(DomainError) as exc_info:
        run_settlement(db, "20260807", today="20260807")

    assert exc_info.value.code == "PENDING_CORPORATE_ACTION"
    with sqlite3.connect(db) as conn:
        after = conn.execute("SELECT COUNT(*) FROM pt_daily_snapshot").fetchone()[0]
    assert after == before

    applied = apply_corporate_action(db, action_id)
    completed = run_settlement(db, "20260807", today="20260807")
    assert applied["status"] == "APPLIED"
    assert completed["snapshot_ok"] is True


def test_scan_result_is_snapshotted_and_creates_a_pool_draft(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO scan_result "
            "(trade_date, ts_code, total_score, price, box_high, box_low, reasons) "
            "VALUES ('20260806', '000002.SZ', 82, 20.2, 20.0, 18.0, "
            "'[池A|strict|验收]')"
        )

    from paper_trading.signals import generate_signal_drafts, sync_signal_snapshots

    synced = sync_signal_snapshots(db, "20260806", regime="neutral")
    drafts = generate_signal_drafts(db, "20260806", today="20260806", regime="neutral")

    assert synced["signals"] == 1
    assert synced["a_pool"] == 1
    assert len(drafts["created"]) == 1
    assert drafts["created"][0]["state"] == "DRAFT"


def test_persistent_idempotency_replays_and_rejects_payload_change(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    from paper_trading.idempotency import execute_idempotent

    calls: list[str] = []

    def operation() -> dict[str, str]:
        calls.append("called")
        return {"result": "created"}

    first = execute_idempotent(db, "acceptance-key", "account.create", {"cash": "100"}, operation)
    replay = execute_idempotent(db, "acceptance-key", "account.create", {"cash": "100"}, operation)

    assert first == replay == {"result": "created"}
    assert calls == ["called"]
    with pytest.raises(DomainError) as exc_info:
        execute_idempotent(db, "acceptance-key", "account.create", {"cash": "101"}, operation)
    assert exc_info.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_real_gate_accepts_explicit_suspension_placeholder() -> None:
    from paper_trading.real_data_gate import _is_valid_bar

    assert _is_valid_bar(0, 0, 0, 10.2, 0, 0) is True
    assert _is_valid_bar(10, 9, 11, 10.2, 1, 1) is False
    assert _is_valid_bar(10, 11, 9, 10.2, -1, 1) is False
