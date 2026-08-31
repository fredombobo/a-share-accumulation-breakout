"""龙虎榜反过拟合 / 容量 / 样本外门禁（T09）。未过关不得称 edge。"""
from __future__ import annotations

from typing import Any

from ab_screener.research.trial_ledger import trial_history

INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
FAIL = "FAIL"
PASS = "PASS"


def validate_research(
    *,
    oos_net_excess: float | None,
    max_drawdown: float | None,
    sample_size: int,
    capacity_ok: bool,
    anti_overfit_passed: bool,
    min_sample: int = 30,
    min_excess: float = 0.0,
    max_dd: float = -0.25,
    shadow_mature_events: int = 0,
    shadow_min_events: int = 30,
    stability_ok: bool = True,
) -> dict[str, Any]:
    reasons: list[str] = []
    if sample_size < min_sample or oos_net_excess is None:
        verdict = INSUFFICIENT
        reasons.append("sample_or_excess_missing")
    elif shadow_mature_events < shadow_min_events:
        verdict = INSUFFICIENT
        reasons.append("shadow_maturity")
    elif not anti_overfit_passed or not capacity_ok or not stability_ok:
        verdict = FAIL
        reasons.append("anti_overfit_or_capacity")
    elif oos_net_excess < min_excess or (max_drawdown is not None and max_drawdown < max_dd):
        verdict = FAIL
        reasons.append("oos_or_drawdown")
    else:
        verdict = PASS
    return {
        "verdict": verdict,
        "reasons": reasons,
        "oos_net_excess": oos_net_excess,
        "max_drawdown": max_drawdown,
        "sample_size": sample_size,
        "shadow_mature_events": shadow_mature_events,
        "can_claim_edge": verdict == PASS,
        "engineering_pass_is_not_edge": True,
    }


def append_trial(registry: list[dict[str, Any]], trial: dict[str, Any]) -> list[dict[str, Any]]:
    """参数试验全量登记，失败/取消也保留，禁止只留最好。"""
    registry.append(dict(trial))
    return registry


def dropped_only_best(registry: list[dict[str, Any]]) -> bool:
    statuses = {str(item.get("status")) for item in registry}
    if not registry:
        return False
    return statuses <= {"COMPLETED", "BEST"} and len(registry) == 1


def record_all_trials(conn, experiment_id: str) -> list[dict[str, Any]]:
    """完整登记，不得只保留最好的。"""
    return trial_history(conn, experiment_id)
