from __future__ import annotations

from ab_screener.research.formal_promotion import apply_formal_promotion_gate
from ab_screener.research.promotion_v2 import ROBUST_PROFILE, STRICT_PROFILE
from ab_screener.research.reporting import render_trusted_report


def _traditional_pass() -> dict:
    return {
        "verdict": "PASS",
        "candidate_eligible": True,
        "summary": "传统门通过",
        "block_reasons": [],
        "checks": [{"id": "oos", "label": "OOS", "passed": True}],
        "primary_oos": {
            "oos_net_total_return": 0.10,
        },
        "baselines": {
            "ma20_60": {"net_total_return": -0.02},
        },
    }


def _request() -> dict:
    return {
        "preregistered": True,
        "promotion_profile": ROBUST_PROFILE,
        "primary_baseline": "ma20_60",
    }


def test_traditional_pass_with_failed_dsr_cannot_create_candidate() -> None:
    report = {
        **_traditional_pass(),
        "v2_statistics": {
            "status": "OK",
            "dsr": 0.0,
            "min_track_record_coverage": 0.188,
        },
    }

    final = apply_formal_promotion_gate(report, _request(), hashes_valid=True)

    assert final["traditional_gate"]["verdict"] == "PASS"
    assert final["verdict"] == "FAIL"
    assert final["candidate_eligible"] is False
    assert final["formal_promotion"]["candidate"] == "NO_CANDIDATE"
    assert "DSR=0.0" in final["summary"]


def test_missing_formal_evidence_fails_closed_with_explicit_profiles() -> None:
    final = apply_formal_promotion_gate(_traditional_pass(), _request(), hashes_valid=True)

    profiles = final["formal_promotion"]["profiles"]
    assert profiles[ROBUST_PROFILE]["verdict"] == "FAIL"
    assert profiles[STRICT_PROFILE]["verdict"] == "FAIL"
    assert "PBO=None" in profiles[ROBUST_PROFILE]["reason"]
    assert "2×" in profiles[ROBUST_PROFILE]["reason"]


def test_complete_robust_evidence_allows_only_isolated_candidate() -> None:
    report = {
        **_traditional_pass(),
        "v2_statistics": {
            "status": "OK",
            "dsr": 0.97,
            "min_track_record_coverage": 1.2,
        },
        "formal_evidence": {
            "version": "formal-evidence-v2",
            "cscv_pbo": {"pbo": 0.15},
            "nested_walkforward": {
                "windows": [{"window": index} for index in range(5)],
                "positive_test_ratio": 0.8,
            },
            "cost_stress": {
                "candidate_net_total_2x": 0.04,
                "baseline_net_total_2x": -0.03,
            },
            "parameter_neighborhood": {"positive_excess_ratio": 0.75, "coverage": 1.0},
        },
    }

    final = apply_formal_promotion_gate(report, _request(), hashes_valid=True)

    assert final["verdict"] == "PASS"
    assert final["candidate_eligible"] is True
    assert final["formal_promotion"]["candidate"] == "CANDIDATE"
    assert "不会自动进入 A 池" in final["summary"]
    markdown = render_trusted_report(final)
    assert "正式统计与最终晋级" in markdown
    assert "ROBUST_PERSONAL_V2" in markdown
