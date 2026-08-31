"""龙虎榜就绪门禁（T12）。工程 PASS ≠ 研究 edge PASS；永不打开实盘旗标。"""
from __future__ import annotations

from typing import Any

from ab_screener.application.platform_config import DEFAULT_FLAGS
from ab_screener.research.lhb_validation import FAIL, INSUFFICIENT, PASS, validate_research

ENGINEERING_PASS = "ENGINEERING_PASS"
RESEARCH_BLOCKED = "RESEARCH_BLOCKED"
RESEARCH_READY = "RESEARCH_READY"
SHADOW_MIN_EVENTS = 30
SHADOW_MIN_MONTHS = 3
SHADOW_PROMOTE_EVENTS = 100
SHADOW_PROMOTE_MONTHS = 6


def evaluate_lhb_readiness(
    *,
    engineering_ok: bool,
    oos_net_excess: float | None,
    max_drawdown: float | None,
    sample_size: int,
    capacity_ok: bool,
    anti_overfit_passed: bool,
    shadow_mature_events: int,
    shadow_months: float,
    stability_ok: bool = True,
    live_trading_enabled: bool | None = None,
    v2_pit_read_enabled: bool | None = None,
    daily_scheduler_enabled: bool | None = None,
) -> dict[str, Any]:
    live = DEFAULT_FLAGS["LIVE_TRADING_ENABLED"] if live_trading_enabled is None else live_trading_enabled
    pit = DEFAULT_FLAGS["V2_PIT_READ_ENABLED"] if v2_pit_read_enabled is None else v2_pit_read_enabled
    sched = (
        DEFAULT_FLAGS["DAILY_SCHEDULER_ENABLED"]
        if daily_scheduler_enabled is None
        else daily_scheduler_enabled
    )
    flags_safe = (live is False) and (pit is False) and (sched is False)
    research = validate_research(
        oos_net_excess=oos_net_excess,
        max_drawdown=max_drawdown,
        sample_size=sample_size,
        capacity_ok=capacity_ok,
        anti_overfit_passed=anti_overfit_passed,
        shadow_mature_events=shadow_mature_events,
        stability_ok=stability_ok,
    )
    shadow_ok = shadow_mature_events >= SHADOW_MIN_EVENTS and shadow_months >= SHADOW_MIN_MONTHS
    if not engineering_ok or not flags_safe:
        overall = "ENGINEERING_BLOCKED"
        research_status = RESEARCH_BLOCKED
    elif research["verdict"] != PASS or not shadow_ok:
        overall = ENGINEERING_PASS
        research_status = RESEARCH_BLOCKED
    else:
        overall = ENGINEERING_PASS
        research_status = RESEARCH_READY
    return {
        "overall": overall,
        "engineering_status": ENGINEERING_PASS if engineering_ok and flags_safe else "ENGINEERING_BLOCKED",
        "research_status": research_status,
        "research_verdict": research["verdict"],
        "can_claim_edge": False if research["verdict"] != PASS else research["can_claim_edge"],
        "may_enter_a_pool": False,
        "may_generate_orders": False,
        "may_enable_live_trading": False,
        "flags": {
            "LIVE_TRADING_ENABLED": live,
            "V2_PIT_READ_ENABLED": pit,
            "DAILY_SCHEDULER_ENABLED": sched,
        },
        "shadow": {
            "mature_events": shadow_mature_events,
            "months": shadow_months,
            "min_events": SHADOW_MIN_EVENTS,
            "min_months": SHADOW_MIN_MONTHS,
            "promote_hint_events": SHADOW_PROMOTE_EVENTS,
            "promote_hint_months": SHADOW_PROMOTE_MONTHS,
            "ok": shadow_ok,
        },
        "research": research,
        "engineering_pass_is_not_edge": True,
        "notes": [
            "工程完成不等于存在可交易 edge",
            "未过样本外/反过拟合/容量/shadow 时保持 RESEARCH_BLOCKED",
            "不得打开 LIVE_TRADING_ENABLED / V2_PIT_READ_ENABLED / DAILY_SCHEDULER_ENABLED",
        ],
        "hard_failures": [
            key
            for key, ok in (
                ("engineering_ok", engineering_ok),
                ("flags_safe", flags_safe),
                ("research_pass", research["verdict"] == PASS),
                ("shadow_ok", shadow_ok),
            )
            if not ok
        ],
        "insufficient": research["verdict"] == INSUFFICIENT,
        "fail": research["verdict"] == FAIL,
    }
