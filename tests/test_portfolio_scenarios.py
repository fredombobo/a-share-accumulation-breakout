"""P5.3 压力情景测试：指数/行业/相关/流动性/成本/停牌/连续跌停。"""
from __future__ import annotations

from ab_screener.application.portfolio_risk import build_portfolio_risk_report
from ab_screener.domain.risk.models import PortfolioState, Position
from ab_screener.domain.risk.scenarios import apply_scenario


def _state() -> PortfolioState:
    return PortfolioState(
        cash_fen=40_000_000, equity_fen=100_000_000,
        positions=(
            Position(ts_code="000001.SZ", qty=1000,
                     latest_close_micro=20_000_000),   # 20 元×1000 = 2 万
            Position(ts_code="600000.SH", qty=2000,
                     latest_close_micro=10_000_000),   # 10 元×2000 = 2 万
        ),
        today="20260810", trade_date="20260810",
    )


def test_scenario_set_and_pnl():
    state = _state()
    market_value = state.market_value_fen()  # 4 万
    s = apply_scenario(state, "INDEX_MINUS_5")
    assert s["pnl_fen"] == -int(market_value * 0.05)
    s3 = apply_scenario(state, "CONSECUTIVE_LIMIT_DOWN")
    assert s3["pnl_fen"] == -int(market_value * 0.30)
    assert apply_scenario(state, "LIQUIDITY_HALVED")["liquidity_hit_pct"] == 0.5


def test_full_report_scenarios():
    state = _state()
    curve = [1.0 * (1.001) ** i + (i % 5) * 0.0001 for i in range(60)]
    report = build_portfolio_risk_report(state, equity_curve=curve,
                                         position_weights=[0.5, 0.5])
    assert "INDEX_MINUS_5" in report["scenarios"]
    assert "COST_3X" in report["scenarios"]
    assert "SUSPENDED" in report["scenarios"]
    assert report["metrics"]["status"] == "OK"
