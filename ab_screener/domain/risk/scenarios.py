"""压力情景（P5.3）：指数/行业/相关组/流动性/成本/停牌情景的组合影响估算。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ab_screener.domain.risk.models import PortfolioState


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    pnl_fen: int = 0
    drawdown_pct: float = 0.0
    liquidity_hit_pct: float = 0.0


def _scenario_pnl(state: PortfolioState, move_pct: float) -> int:
    """持仓市值 × 跌幅（保守：全部持仓同向受冲击）。"""
    value = state.market_value_fen()
    return -int(value * abs(move_pct))


def apply_scenario(state: PortfolioState, scenario_name: str) -> dict[str, Any]:
    """返回 {name, pnl_fen, drawdown_pct, notes}。"""
    scenarios = {
        "INDEX_MINUS_5": Scenario("INDEX_MINUS_5", "指数 -5%", _scenario_pnl(state, 0.05), 0.05),
        "INDUSTRY_MINUS_10": Scenario("INDUSTRY_MINUS_10", "行业 -10%（全部持仓视为同行业保守估算）",
                                      _scenario_pnl(state, 0.10), 0.10),
        "CORRELATED_DOWN": Scenario("CORRELATED_DOWN", "相关持仓同步下跌 15%",
                                    _scenario_pnl(state, 0.15), 0.15),
        "LIQUIDITY_HALVED": Scenario("LIQUIDITY_HALVED", "流动性腰斩（退出天数 ×2）",
                                     0, 0.0, 0.5),
        "COST_1X": Scenario("COST_1X", "1× 成本", 0, 0.0),
        "COST_2X": Scenario("COST_2X", "2× 成本", 0, 0.0),
        "COST_3X": Scenario("COST_3X", "3× 成本", 0, 0.0),
        "SUSPENDED": Scenario("SUSPENDED", "停牌（无法卖出，估值冻结）", 0, 0.0),
        "CONSECUTIVE_LIMIT_DOWN": Scenario("CONSECUTIVE_LIMIT_DOWN", "连续跌停（-10%×3 保守）",
                                           _scenario_pnl(state, 0.30), 0.30),
    }
    scenario = scenarios[scenario_name]
    equity = state.equity_fen or 1
    return {
        "name": scenario.name,
        "description": scenario.description,
        "pnl_fen": scenario.pnl_fen,
        "drawdown_pct": round(scenario.drawdown_pct, 4),
        "drawdown_ratio": round(abs(scenario.pnl_fen) / equity, 4),
        "liquidity_hit_pct": scenario.liquidity_hit_pct,
    }
