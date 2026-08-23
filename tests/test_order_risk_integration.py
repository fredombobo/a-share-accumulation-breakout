"""P5.1 纸面订单风险集成测试：adapter 从 pt DB 评估，observe 模式。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from paper_trading import risk_adapter
from paper_trading.account import create_account
from paper_trading.risk_adapter import evaluate_order_risk


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "risk.db"
    from local_store import LocalStore

    LocalStore(db_path=path)
    create_account(path, 50_000_000)  # 500 万
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO daily (ts_code, trade_date, open, high, low, close, vol, amount)"
            " VALUES ('000001.SZ','20260810',10.0,10.5,9.8,10.2,1000000,1e7)"
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


def test_review_uses_same_check_as_confirm_path(db: str):
    """Review 与 confirm 共用同一评估入口（observe 模式返回违规不阻断）。"""
    result = evaluate_order_risk(
        db, ts_code="000001.SZ", side="BUY", qty=100,
        price_micro=10_000_000, today="20260810",
    )
    assert result["mode"] == "observe"
    assert result["blocked"] is False
    assert isinstance(result["violations"], list)
    codes = {v["code"] for v in result["violations"]}
    # 500 万账户买入 1000 元：现金充足但最低现金 10%（50 万）不触发；单票占比低
    assert "RISK_CASH_INSUFFICIENT" not in codes


def test_large_buy_flagged_observe_not_blocked(db: str):
    """大额买入被风险码标记（observe），但不由风险层硬阻断（enforce 后再阻断）。"""
    result = evaluate_order_risk(
        db, ts_code="000001.SZ", side="BUY", qty=4_000_000,  # 4000 万 > 账户 500 万
        price_micro=10_000_000, today="20260810",
    )
    codes = {v["code"] for v in result["violations"]}
    assert "RISK_CASH_INSUFFICIENT" in codes
    assert result["blocked"] is False  # observe


def test_sell_risk_no_buy_concentration_blocks(db: str):
    result = evaluate_order_risk(
        db, ts_code="000001.SZ", side="SELL", qty=100,
        price_micro=10_000_000, today="20260810",
    )
    buy_codes = {"RISK_SINGLE_NAME_LIMIT", "RISK_MIN_CASH", "RISK_DAILY_ADDITION_LIMIT"}
    codes = {v["code"] for v in result["violations"]}
    assert not (buy_codes & codes)


def test_enforce_mode_blocks_when_flagged(db: str, monkeypatch) -> None:
    """V2_RISK_ENFORCEMENT_ENABLED=true 时：有违规 → blocked=True（enforce）。"""
    monkeypatch.setattr(risk_adapter, "_enforcement_enabled", lambda: True)
    result = evaluate_order_risk(
        db, ts_code="000001.SZ", side="BUY", qty=4_000_000,
        price_micro=10_000_000, today="20260810",
    )
    assert result["mode"] == "enforce"
    assert "RISK_CASH_INSUFFICIENT" in {v["code"] for v in result["violations"]}
    assert result["blocked"] is True


def test_enforce_mode_fail_closed_on_risk_error(db: str, monkeypatch) -> None:
    """enforce 模式下风控评估异常 → fail-closed（blocked=True，不静默放行）。"""
    monkeypatch.setattr(risk_adapter, "_enforcement_enabled", lambda: True)

    def _boom(*_a, **_k):
        raise RuntimeError("portfolio state unavailable")

    monkeypatch.setattr(risk_adapter, "build_portfolio_state", _boom)
    result = evaluate_order_risk(
        db, ts_code="000001.SZ", side="BUY", qty=100,
        price_micro=10_000_000, today="20260810",
    )
    assert result["mode"] == "enforce"
    assert result["blocked"] is True
    assert result["violations"][0]["code"] == "RISK_UNAVAILABLE"


def test_observe_mode_returns_structured_degradation_not_raise(db: str, monkeypatch) -> None:
    """observe 模式评估异常 → 结构化降级（degraded=True/blocked=False），不抛出。"""
    monkeypatch.setattr(risk_adapter, "_enforcement_enabled", lambda: False)

    def _boom(*_a, **_k):
        raise RuntimeError("risk backend down")

    monkeypatch.setattr(risk_adapter, "build_portfolio_state", _boom)
    result = evaluate_order_risk(
        db, ts_code="000001.SZ", side="BUY", qty=100,
        price_micro=10_000_000, today="20260810",
    )
    assert result["mode"] == "observe"
    assert result["degraded"] is True
    assert result["blocked"] is False
    assert result["violations"][0]["code"] == "RISK_UNAVAILABLE"
    # 结构化四键始终存在
    for key in ("blocked", "mode", "violations", "degraded"):
        assert key in result
