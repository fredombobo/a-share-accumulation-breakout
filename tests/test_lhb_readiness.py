"""T12 就绪：工程 PASS ≠ edge PASS；旗标保持关闭。"""
from __future__ import annotations

from ab_screener.application.lhb_readiness import (
    ENGINEERING_PASS,
    RESEARCH_BLOCKED,
    evaluate_lhb_readiness,
)
from ab_screener.application.platform_config import DEFAULT_FLAGS


def test_flags_remain_safe():
    assert DEFAULT_FLAGS["LIVE_TRADING_ENABLED"] is False
    assert DEFAULT_FLAGS["V2_PIT_READ_ENABLED"] is False
    assert DEFAULT_FLAGS["DAILY_SCHEDULER_ENABLED"] is False


def test_engineering_pass_research_blocked_without_shadow():
    out = evaluate_lhb_readiness(
        engineering_ok=True,
        oos_net_excess=0.02,
        max_drawdown=-0.1,
        sample_size=80,
        capacity_ok=True,
        anti_overfit_passed=True,
        shadow_mature_events=5,
        shadow_months=1.0,
    )
    assert out["engineering_status"] == ENGINEERING_PASS
    assert out["research_status"] == RESEARCH_BLOCKED
    assert out["can_claim_edge"] is False
    assert out["may_enter_a_pool"] is False
    assert out["may_generate_orders"] is False
    assert out["may_enable_live_trading"] is False
    assert out["engineering_pass_is_not_edge"] is True
    assert out["flags"]["LIVE_TRADING_ENABLED"] is False


def test_any_hard_research_fail_is_blocked():
    out = evaluate_lhb_readiness(
        engineering_ok=True,
        oos_net_excess=-0.05,
        max_drawdown=-0.4,
        sample_size=80,
        capacity_ok=False,
        anti_overfit_passed=False,
        shadow_mature_events=200,
        shadow_months=12.0,
    )
    assert out["research_status"] == RESEARCH_BLOCKED
    assert out["can_claim_edge"] is False
    assert "research_pass" in out["hard_failures"] or out["fail"] is True
