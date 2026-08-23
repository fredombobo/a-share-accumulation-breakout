"""V2R-X：review 与 confirm 共享统一风控入口（不信前端提交的风控结果）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from paper_trading.account import create_account
from paper_trading.guidance import review_order
from tests.paper_market_fixture import seed_fresh_neutral_benchmark


def _setup(db: Path) -> None:
    from local_store import LocalStore

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
    create_account(db, 50_000_000)


def test_review_includes_unified_risk_check(tmp_path: Path) -> None:
    """review_order 的 checks 含统一风控项（与 confirm 共享入口）。"""
    db = tmp_path / "review-risk.db"
    _setup(db)

    review = review_order(
        db, scope="ACCOUNT", side="BUY", mode="MANUAL_HISTORY",
        ts_code="000001", execution_trade_date="20260806", qty=100,
    )

    codes = {c["code"] for c in review["checks"]}
    assert "RISK" in codes, "review 应包含统一风控检查项"


def test_review_calls_unified_risk_entry(monkeypatch, tmp_path: Path) -> None:
    """monkeypatch evaluate_order_risk：review_order 必须调用它（共享入口）。"""
    db = tmp_path / "parity.db"
    _setup(db)

    calls: list[dict] = []
    import paper_trading.risk_adapter as ra

    def spy(db_path, **kw):
        calls.append(kw)
        return {"ts_code": kw["ts_code"], "side": kw["side"], "violations": [],
                "blocked": False, "mode": "observe"}

    monkeypatch.setattr(ra, "evaluate_order_risk", spy)

    review_order(
        db, scope="ACCOUNT", side="BUY", mode="MANUAL_HISTORY",
        ts_code="000001", execution_trade_date="20260806", qty=100,
    )

    assert calls, "review_order 应调用统一风控入口 evaluate_order_risk"
    assert calls[0]["ts_code"] == "000001.SZ"
    assert calls[0]["side"] == "BUY"
    assert calls[0]["qty"] == 100


def test_confirm_calls_unified_risk_entry(monkeypatch, tmp_path: Path) -> None:
    """monkeypatch evaluate_order_risk：confirm_order 必须调用它（不信前端风控结果）。"""
    db = tmp_path / "confirm-risk.db"
    _setup(db)

    # 造一个 historical DRAFT 买入订单（跳过 A 池信号检查，聚焦风控接线）
    from paper_trading.orders import create_historical_buy_draft

    draft = create_historical_buy_draft(
        db, ts_code="000001.SZ", execution_trade_date="20260806", qty=100,
    )

    calls: list[dict] = []
    import paper_trading.risk_adapter as ra

    def spy(db_path, **kw):
        calls.append(kw)
        return {"ts_code": kw["ts_code"], "side": kw["side"], "violations": [],
                "blocked": False, "mode": "observe"}

    monkeypatch.setattr(ra, "evaluate_order_risk", spy)

    # confirm 路径会调用 evaluate_order_risk（在规则检查后）；注入 spy 观察
    import paper_trading.orders as _orders
    try:
        _orders.confirm_order(db, draft["order_id"], today="20260807")
    except Exception:
        pass

    assert calls, "confirm_order 应调用统一风控入口 evaluate_order_risk"
    assert calls[0]["ts_code"] == "000001.SZ"
