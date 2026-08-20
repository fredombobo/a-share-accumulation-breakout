"""极端压力情景库（C 阶段）：2008/2015/2020 参数化情景。

- 参数化模型（非历史精确回放）：各情景声明指数冲击、流动性、波动放大。
- 组合冲击 = 持仓市值 × 情景系数（保守全仓同向）。
- 诚实声明：参数为研究者设定，非历史数据拟合。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ab_screener.domain.risk.models import PortfolioState


@dataclass(frozen=True)
class StressScenario:
    name: str
    description: str
    index_shock_pct: float     # 指数级冲击（损失为正）
    vol_multiplier: float      # 波动放大倍数
    liquidity_hit_pct: float   # 流动性收缩比例
    recovery_note: str


SCENARIOS: dict[str, StressScenario] = {
    "GFC_2008": StressScenario(
        name="GFC_2008", description="2008 金融危机参数化：指数 -55%、流动性冻结、波动 ×3",
        index_shock_pct=0.55, vol_multiplier=3.0, liquidity_hit_pct=0.8,
        recovery_note="深度且漫长（>1 年）",
    ),
    "LEVERAGE_2015": StressScenario(
        name="LEVERAGE_2015", description="2015 杠杆崩盘参数化：指数 -45%、流动性骤停、波动 ×2.5",
        index_shock_pct=0.45, vol_multiplier=2.5, liquidity_hit_pct=0.7,
        recovery_note="剧烈但相对快速（数月）",
    ),
    "COVID_2020": StressScenario(
        name="COVID_2020", description="2020 疫情冲击参数化：指数 -35%、快速冲击、波动 ×2",
        index_shock_pct=0.35, vol_multiplier=2.0, liquidity_hit_pct=0.5,
        recovery_note="冲击快、V 型反转（数周-数月）",
    ),
}


def apply_extreme_scenario(
    state: PortfolioState,
    scenario_name: str,
) -> dict[str, Any]:
    """组合在指定极端情景下的冲击估算（参数化保守口径）。"""
    scenario = SCENARIOS.get(scenario_name)
    if scenario is None:
        raise ValueError(f"未知压力情景: {scenario_name}（可选: {sorted(SCENARIOS)}）")
    market_value = state.market_value_fen()
    loss_fen = -int(market_value * scenario.index_shock_pct)
    equity = state.equity_fen or 1
    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "index_shock_pct": scenario.index_shock_pct,
        "vol_multiplier": scenario.vol_multiplier,
        "liquidity_hit_pct": scenario.liquidity_hit_pct,
        "estimated_pnl_fen": loss_fen,
        "drawdown_ratio": round(abs(loss_fen) / equity, 4),
        "recovery_note": scenario.recovery_note,
        "assumption_note": "参数化模型（非历史精确回放）；保守假设全部持仓同向受冲击",
    }


def extreme_scenario_report(state: PortfolioState) -> dict[str, Any]:
    """全部极端情景汇总。"""
    return {
        "scenarios": {
            name: apply_extreme_scenario(state, name) for name in SCENARIOS
        },
        "market_value_fen": state.market_value_fen(),
        "equity_fen": state.equity_fen,
    }
