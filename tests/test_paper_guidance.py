"""Beginner guidance and read-only review tests for paper trading."""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from local_store import LocalStore
from paper_trading.account import create_account
from paper_trading.errors import DomainError
from paper_trading.guidance import build_guide, review_order, trading_calendar
from paper_trading.orders import confirm_order, create_historical_buy_draft
from paper_trading.settlement import run_settlement
from tests.paper_market_fixture import seed_fresh_neutral_benchmark


def _setup(db: Path, *, with_account: bool = True) -> None:
    LocalStore(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO daily (ts_code, trade_date, open, high, low, close, vol, amount)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ("000001.SZ", "20260805", 10.0, 10.2, 9.9, 10.1, 10_000, 101_000),
                ("000001.SZ", "20260806", 10.2, 10.5, 10.1, 10.4, 10_000, 104_000),
                ("000001.SZ", "20260807", 10.4, 10.6, 10.3, 10.5, 10_000, 105_000),
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (cal_date,is_open,source,updated_at)"
            " VALUES (?,?, 'tushare','t')",
            [
                ("20260805", 1), ("20260806", 1), ("20260807", 1),
                ("20260808", 0), ("20260809", 0), ("20260810", 1),
            ],
        )
        seed_fresh_neutral_benchmark(conn)
    if with_account:
        create_account(db, 50_000_000)


def _pt_snapshot(db: Path) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pt_%'"
            )
        ]
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in sorted(tables)}


def test_tutorial_review_uses_local_open_and_writes_no_business_rows(tmp_path: Path) -> None:
    db = tmp_path / "tutorial.db"
    _setup(db, with_account=False)
    before = _pt_snapshot(db)

    review = review_order(
        db,
        scope="TUTORIAL",
        side="BUY",
        mode="MANUAL_HISTORY",
        ts_code="000001",
        execution_trade_date="20260806",
        qty=100,
    )

    assert review["persisted"] is False
    assert review["scope"] == "TUTORIAL"
    assert review["instrument"]["ts_code"] == "000001.SZ"
    assert review["decision_date"] == "20260805"
    assert review["execution_trade_date"] == "20260806"
    assert review["quote"]["open"] == "10.200000"
    assert review["estimate"]["fill_price"] == "10.210200"
    assert review["estimate"]["commission_yuan"] == "5.00"
    assert review["estimate"]["estimated_fill_qty"] == 100
    assert review["can_confirm"] is True
    assert _pt_snapshot(db) == before


def test_account_review_reserve_matches_formal_confirmation(tmp_path: Path) -> None:
    db = tmp_path / "account-review.db"
    _setup(db)

    review = review_order(
        db,
        scope="ACCOUNT",
        side="BUY",
        mode="MANUAL_HISTORY",
        ts_code="000001.SZ",
        execution_trade_date="20260806",
        qty=100,
    )
    order = create_historical_buy_draft(
        db, ts_code="000001.SZ", execution_trade_date="20260806", qty=100,
    )
    confirmed = confirm_order(db, order["order_id"])

    review_reserve_fen = int(Decimal(review["estimate"]["reserve_yuan"]) * 100)
    assert review_reserve_fen == confirmed["reserve_fen"]
    assert review["can_confirm"] is True


def test_account_review_rejects_duplicate_active_buy_before_writing(tmp_path: Path) -> None:
    db = tmp_path / "duplicate-review.db"
    _setup(db)
    order = create_historical_buy_draft(
        db, ts_code="000001.SZ", execution_trade_date="20260806", qty=100,
    )
    confirm_order(db, order["order_id"])
    before = _pt_snapshot(db)

    with pytest.raises(DomainError) as exc_info:
        review_order(
            db,
            scope="ACCOUNT",
            side="BUY",
            mode="MANUAL_HISTORY",
            ts_code="000001.SZ",
            execution_trade_date="20260807",
            qty=100,
        )

    assert exc_info.value.code == "DUPLICATE_ACTIVE_ORDER"
    assert _pt_snapshot(db) == before


def test_review_explains_closed_day_without_creating_order(tmp_path: Path) -> None:
    db = tmp_path / "closed.db"
    _setup(db)
    before = _pt_snapshot(db)

    with pytest.raises(DomainError) as exc_info:
        review_order(
            db,
            scope="ACCOUNT",
            side="BUY",
            mode="MANUAL_HISTORY",
            ts_code="000001",
            execution_trade_date="20260808",
            qty=100,
        )

    assert exc_info.value.code == "NOT_TRADING_DAY"
    assert _pt_snapshot(db) == before


def test_guide_and_calendar_return_one_clear_next_action(tmp_path: Path) -> None:
    db = tmp_path / "guide.db"
    _setup(db)

    guide = build_guide(db)
    calendar = trading_calendar(db, start="20260805", end="20260810")

    assert guide["next_action"] == "START_SIMULATION"
    assert guide["latest_market_date"] == "20260807"
    assert guide["pending_order"] is None
    assert calendar["open_dates"] == ["20260805", "20260806", "20260807", "20260810"]
    assert calendar["latest_market_date"] == "20260807"


def test_guide_prioritizes_draft_then_confirmed_order(tmp_path: Path) -> None:
    db = tmp_path / "guide-pending.db"
    _setup(db)
    order = create_historical_buy_draft(
        db, ts_code="000001.SZ", execution_trade_date="20260806", qty=100,
    )

    draft_guide = build_guide(db)
    assert draft_guide["next_action"] == "REVIEW_DRAFT"
    assert draft_guide["pending_order"]["order_id"] == order["order_id"]

    confirm_order(db, order["order_id"])
    confirmed_guide = build_guide(db)
    assert confirmed_guide["next_action"] == "RUN_SETTLEMENT"
    assert confirmed_guide["pending_order"]["state"] == "CONFIRMED"


def test_guide_requests_data_sync_when_ledger_floor_is_after_latest_quote(tmp_path: Path) -> None:
    db = tmp_path / "guide-sync.db"
    _setup(db)
    order = create_historical_buy_draft(
        db, ts_code="000001.SZ", execution_trade_date="20260807", qty=100,
    )
    confirm_order(db, order["order_id"])
    run_settlement(db, "20260807", today="20260807")

    guide = build_guide(db)

    assert guide["earliest_simulation_date"] == "20260810"
    assert guide["latest_market_date"] == "20260807"
    assert guide["next_action"] == "SYNC_DATA"
    assert "LEDGER_AHEAD_OF_MARKET" in guide["blocker_codes"]
