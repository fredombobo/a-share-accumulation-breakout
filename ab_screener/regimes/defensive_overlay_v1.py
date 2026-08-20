"""防守 overlay v1（P4.1）：只改变开仓许可，不产生买单。

规则：市场防守 regime 或组合回撤超阈值 → 禁止新开仓（allow_new_entries=False）；
人工覆盖优先。
"""
from __future__ import annotations

from ab_screener.regimes.contracts import OverlayDecision, OverlayInput
from ab_screener.regimes.registry import register_regime_overlay
from ab_screener.strategies.contracts import StrategySpec

DEFENSIVE_OVERLAY_ID = "defensive_overlay_v1"

SPEC = StrategySpec(
    strategy_definition_id=DEFENSIVE_OVERLAY_ID,
    version="v1",
    economic_assumption="防守环境下开仓胜率与风险回报显著恶化，暂停新开仓保护组合",
    failure_conditions="防守判定滞后（regime 切换延迟）/ 回撤阈值过宽导致保护不足",
    pit_test="只使用当日前已 available 的市场/组合数据",
    golden_fixture="tests/fixtures/defensive_overlay_v1_golden.json（待生成）",
    config_path="configs/regimes/defensive_overlay_v1.yaml",
)


def evaluate(
    overlay_input: OverlayInput,
    config: dict | None = None,
) -> OverlayDecision:
    """防守判定：人工覆盖优先 → 防守 regime/回撤阈值 → 默认放行。"""
    if overlay_input.allow_new_entries_override is not None:
        return OverlayDecision(
            allow_new_entries=overlay_input.allow_new_entries_override,
            reason="人工覆盖",
            mode="neutral",
        )
    cfg = config or {}
    max_drawdown = float(cfg.get("max_drawdown_before_block", 0.10))
    if overlay_input.market_regime == "defensive":
        return OverlayDecision(
            allow_new_entries=False,
            reason=f"市场 regime=defensive（{overlay_input.market_regime}）",
            mode="defensive",
        )
    if overlay_input.drawdown_from_peak >= max_drawdown:
        return OverlayDecision(
            allow_new_entries=False,
            reason=f"回撤 {overlay_input.drawdown_from_peak:.1%} ≥ {max_drawdown:.0%}",
            mode="defensive",
        )
    return OverlayDecision(allow_new_entries=True, reason="市场环境允许开仓", mode="neutral")


register_regime_overlay(DEFENSIVE_OVERLAY_ID, SPEC, evaluate)
