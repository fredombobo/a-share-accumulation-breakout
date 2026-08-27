"""P5.2 风险快照 PIT 测试：append-only、版本字段、未来行情不影响历史快照。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.risk_repository import (
    RiskRepositoryError,
    latest_risk_snapshot,
    save_risk_snapshot,
)
from ab_screener.local_store import LocalStore
from paper_trading.account import commit_import, create_account
from paper_trading.migrations import run_migrations
from paper_trading.risk_adapter import build_portfolio_state


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "risk.db"))
    apply_pending(c)
    yield c
    c.close()


def test_save_and_latest_snapshot(conn):
    sid = save_risk_snapshot(
        conn, trade_date="20260810", market_version="daily:20260810",
        metrics={"status": "OK", "sharpe_annual": 1.2},
        scenarios={"INDEX_MINUS_5": {"pnl_fen": -100}},
    )
    latest = latest_risk_snapshot(conn, trade_date="20260810")
    assert latest["snapshot_id"] == sid
    assert latest["rule_version"] == "risk-v2"
    assert latest["config_version"] == "robust_personal_v2"
    assert latest["metrics"]["sharpe_annual"] == 1.2
    # 幂等：同内容再存返回同 id
    sid2 = save_risk_snapshot(
        conn, trade_date="20260810", market_version="daily:20260810",
        metrics={"status": "OK", "sharpe_annual": 1.2},
        scenarios={"INDEX_MINUS_5": {"pnl_fen": -100}},
    )
    assert sid2 == sid


def test_snapshot_append_only(conn):
    sid = save_risk_snapshot(conn, trade_date="20260810", market_version="m1",
                             metrics={"a": 1}, scenarios={})
    with pytest.raises(Exception, match="append-only"):
        conn.execute("UPDATE risk_snapshots SET metrics_json='{}' WHERE snapshot_id=?", (sid,))
    conn.rollback()


def test_future_market_does_not_change_historical_snapshot(conn):
    """未来行情注入不能改变历史风险快照（快照已固化，含 market_version）。"""
    save_risk_snapshot(conn, trade_date="20260810", market_version="daily:20260810",
                       metrics={"sharpe_annual": 1.0}, scenarios={})
    # 注入未来行情后重新保存同日快照（不同 market_version）
    save_risk_snapshot(conn, trade_date="20260810", market_version="daily:20260820",
                       metrics={"sharpe_annual": 2.0}, scenarios={})
    # 按 trade_date 取最新 → 是第二次的
    latest = latest_risk_snapshot(conn, trade_date="20260810")
    assert latest["market_version"] == "daily:20260820"
    # 历史快照不可变：仍能读到第一次（append-only 保留）
    n = conn.execute("SELECT COUNT(*) FROM risk_snapshots WHERE trade_date='20260810'").fetchone()[0]
    assert n == 2


def test_missing_table_fail_closed(tmp_path: Path):
    empty = sqlite3.connect(str(tmp_path / "naked.db"))
    try:
        with pytest.raises(RiskRepositoryError, match="表不存在"):
            save_risk_snapshot(empty, trade_date="d", market_version="m",
                               metrics={}, scenarios={})
    finally:
        empty.close()


def test_portfolio_state_does_not_read_future_close(tmp_path: Path) -> None:
    """历史风险状态严格使用 as-of 价格，未来行情不能污染过去权益。"""
    db = tmp_path / "paper.db"
    LocalStore(db_path=db)
    run_migrations(db)
    with sqlite3.connect(db) as connection:
        apply_pending(connection)
    with sqlite3.connect(db) as connection:
        connection.executemany(
            "INSERT INTO daily (ts_code,trade_date,open,high,low,close,vol,amount)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ("000001.SZ", "20260810", 10, 10, 10, 10, 1000, 10000),
                ("000001.SZ", "20260820", 99, 99, 99, 99, 1000, 99000),
            ],
        )
        connection.commit()
    create_account(db, 1_000_000)
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(
        '{"positions":[{"ts_code":"000001.SZ","cost":8,"shares":100,'
        '"opened_at":"2026-08-01T10:00:00+08:00"}]}',
        encoding="utf-8",
    )
    commit_import(db, str(portfolio), as_of_date="20260809")

    state = build_portfolio_state(db, today="20260810")

    assert state.positions[0].latest_close_micro == 10_000_000
    assert state.equity_fen == 1_000_000 + 100_000
