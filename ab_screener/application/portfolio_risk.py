"""组合风险应用（P5.2）：指标 + 情景 + 快照组装。"""
from __future__ import annotations

from typing import Any

from ab_screener.domain.risk.analytics import (
    concentration,
    liquidity_days,
    portfolio_metrics,
)
from ab_screener.domain.risk.models import PortfolioState
from ab_screener.domain.risk.scenarios import apply_scenario

SCENARIO_NAMES = (
    "INDEX_MINUS_5", "INDUSTRY_MINUS_10", "CORRELATED_DOWN", "LIQUIDITY_HALVED",
    "COST_1X", "COST_2X", "COST_3X", "SUSPENDED", "CONSECUTIVE_LIMIT_DOWN",
)


def build_portfolio_risk_report(
    state: PortfolioState,
    *,
    equity_curve: list[float] | None = None,
    position_weights: list[float] | None = None,
    daily_capacity_fen: int = 0,
    scenarios: tuple[str, ...] = SCENARIO_NAMES,
) -> dict[str, Any]:
    """组装风险报告：指标（权益曲线口径）+ 集中度 + 流动性 + 压力情景。"""
    metrics = portfolio_metrics(equity_curve or []) if equity_curve else {
        "status": "INSUFFICIENT", "reason": "未提供权益曲线"
    }
    concentration_report = concentration(position_weights or []) if position_weights else {
        "status": "INSUFFICIENT", "reason": "未提供持仓权重"
    }
    liquidity = liquidity_days(state.market_value_fen(), daily_capacity_fen)
    scenario_results = {
        name: apply_scenario(state, name)
        for name in scenarios
        if name in SCENARIO_NAMES
    }
    return {
        "metrics": metrics,
        "concentration": concentration_report,
        "liquidity_days": liquidity,
        "scenarios": scenario_results,
        "regime": state.regime,
        "equity_fen": state.equity_fen,
        "cash_fen": state.cash_fen,
    }
