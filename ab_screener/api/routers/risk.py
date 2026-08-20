"""组合风险 API（P7.1）：风险报告 + 只读压力计算（side_effects=false）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ab_screener.application.portfolio_risk import (
    SCENARIO_NAMES,
    build_portfolio_risk_report,
)
from ab_screener.domain.risk.models import PortfolioState, Position

router = APIRouter(prefix="/api/v2/portfolio", tags=["portfolio-risk"])


def _state_from_weights(cash_weight: float, weights: list[float]) -> PortfolioState:
    """从权重构造只读风险状态（估值用名义单位；无账本访问）。"""
    equity = 100_000_000
    cash = int(equity * cash_weight)
    positions = tuple(
        Position(ts_code=f"POS-{i}", qty=int(w * 1_000_000),
                 latest_close_micro=10_000_000)
        for i, w in enumerate(weights)
    )
    return PortfolioState(cash_fen=cash, equity_fen=equity, positions=positions,
                          today="", trade_date="")


@router.get("/risk")
def portfolio_risk(
    cash_weight: float = 0.3,
    weights: str = "0.4,0.2,0.1",
    db_path: str | None = None,
) -> dict[str, Any]:
    """暴露、风险、流动性和证据不足项（只读；权益曲线缺省 → INSUFFICIENT）。"""
    weight_list = [float(x) for x in weights.split(",") if x]
    state = _state_from_weights(cash_weight, weight_list)
    return build_portfolio_risk_report(
        state, equity_curve=None, position_weights=weight_list,
    )


@router.post("/stress")
def stress(scenario_names: list[str] | None = None) -> dict[str, Any]:
    """只读压力计算（side_effects=false，不改账本）。"""
    state = _state_from_weights(0.3, [0.4, 0.2, 0.1])
    names = scenario_names or list(SCENARIO_NAMES)
    report = build_portfolio_risk_report(state, equity_curve=None, scenarios=tuple(names))
    return {"side_effects": False, "scenarios": report["scenarios"]}
