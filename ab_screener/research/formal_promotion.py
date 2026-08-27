"""Final fail-closed promotion gate for authoritative Breakout research."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ab_screener.research.promotion_v2 import (
    ROBUST_PROFILE,
    PromotionEvidence,
    promotion_decision,
)

FORMAL_PROMOTION_GATE_VERSION = "formal-promotion-gate-v2.0.0"
PRIMARY_BASELINES = {"ma20_60", "random"}


def promotion_evidence_from_report(
    report: dict[str, Any],
    request: dict[str, Any],
    *,
    hashes_valid: bool,
) -> PromotionEvidence:
    """Assemble only explicit evidence; absent formal blocks remain absent and fail."""
    statistics = _mapping(report.get("v2_statistics"))
    formal = _mapping(report.get("formal_evidence"))
    pbo = _mapping(formal.get("cscv_pbo"))
    nested = _mapping(formal.get("nested_walkforward"))
    stress = _mapping(formal.get("cost_stress"))
    neighborhood = _mapping(formal.get("parameter_neighborhood"))
    primary_oos = _mapping(report.get("primary_oos"))
    baselines = _mapping(report.get("baselines"))
    primary_baseline = str(request.get("primary_baseline") or "")
    baseline = _mapping(baselines.get(primary_baseline))
    raw_nested_windows = nested.get("windows")
    nested_windows: list[Any] = raw_nested_windows if isinstance(raw_nested_windows, list) else []

    preregistered = (
        request.get("preregistered") is True
        and request.get("promotion_profile") == ROBUST_PROFILE
        and primary_baseline in PRIMARY_BASELINES
    )
    return PromotionEvidence(
        pbo=_number(pbo.get("pbo")),
        dsr=_number(statistics.get("dsr")) if statistics.get("status") == "OK" else None,
        min_track_record_coverage=(
            _number(statistics.get("min_track_record_coverage")) if statistics.get("status") == "OK" else None
        ),
        outer_test_windows=len(nested_windows),
        positive_test_ratio=_number(nested.get("positive_test_ratio")) or 0.0,
        oos_net_total=_number(primary_oos.get("oos_net_total_return", primary_oos.get("oos_net_avg_return"))),
        baseline_net_total=_number(baseline.get("net_total_return", baseline.get("net_avg_return"))),
        oos_net_2x=_number(stress.get("candidate_net_total_2x")),
        baseline_net_2x=_number(stress.get("baseline_net_total_2x")),
        neighborhood_positive_ratio=_number(neighborhood.get("positive_excess_ratio")),
        neighborhood_coverage=_number(neighborhood.get("coverage")),
        hashes_valid=bool(hashes_valid),
        preregistered=preregistered,
        checks={
            "primary_baseline": primary_baseline or None,
            "statistics_status": statistics.get("status"),
            "formal_evidence_version": formal.get("version"),
        },
    )


def apply_formal_promotion_gate(
    report: dict[str, Any],
    request: dict[str, Any],
    *,
    hashes_valid: bool,
) -> dict[str, Any]:
    """Make ROBUST_PERSONAL_V2 the final candidate gate after the traditional gate."""
    result = deepcopy(report)
    traditional = {
        key: deepcopy(result.get(key))
        for key in ("verdict", "candidate_eligible", "summary", "block_reasons", "checks")
    }
    evidence = promotion_evidence_from_report(result, request, hashes_valid=hashes_valid)
    decision = promotion_decision(evidence)
    decision["version"] = FORMAL_PROMOTION_GATE_VERSION
    decision["evidence"]["primary_baseline"] = evidence.checks.get("primary_baseline")
    decision["evidence"]["hashes_valid"] = evidence.hashes_valid
    decision["evidence"]["preregistered"] = evidence.preregistered
    robust = decision["profiles"][ROBUST_PROFILE]
    formal_check = {
        "id": "formal_promotion_robust_personal_v2",
        "label": "ROBUST_PERSONAL_V2 正式晋级门",
        "passed": bool(robust["pass"]),
        "actual": decision["evidence"],
        "threshold": "PBO/DSR/MinTRL/5窗/2×成本/邻域/身份全部通过",
    }

    result["traditional_gate"] = traditional
    result["formal_promotion"] = decision
    result["checks"] = list(traditional.get("checks") or []) + [formal_check]
    if traditional.get("verdict") == "PASS" and not robust["pass"]:
        reason = f"正式晋级门未通过：{robust['reason']}"
        result["verdict"] = "FAIL"
        result["candidate_eligible"] = False
        result["block_reasons"] = list(traditional.get("block_reasons") or []) + [reason]
        result["summary"] = reason
    elif traditional.get("verdict") == "PASS" and robust["pass"]:
        result["verdict"] = "PASS"
        result["candidate_eligible"] = True
        result["block_reasons"] = []
        result["summary"] = "通过 ROBUST_PERSONAL_V2，仅允许登记为隔离候选，不会自动进入 A 池或下单"
    else:
        result["candidate_eligible"] = False
    return result


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
