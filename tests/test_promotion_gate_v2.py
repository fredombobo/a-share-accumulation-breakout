"""P3.4 晋级门禁测试：ROBUST/STRICT 双口径、CANDIDATE 语义。"""
from __future__ import annotations

from ab_screener.research.promotion_v2 import (
    ROBUST_PROFILE,
    STRICT_PROFILE,
    PromotionEvidence,
    evaluate_robust_personal_v2,
    evaluate_strict_research_v2,
    promotion_decision,
)


def _good_evidence(**over) -> PromotionEvidence:
    ev = PromotionEvidence(
        pbo=0.15, dsr=0.97, min_track_record_coverage=2,
        outer_test_windows=6, positive_test_ratio=0.83,
        oos_net_total=1_000_000, baseline_net_total=800_000,
        oos_net_2x=300_000, neighborhood_positive_ratio=0.70,
        hashes_valid=True, preregistered=True,
    )
    return PromotionEvidence(**{**ev.__dict__, **over})


def test_robust_pass_when_all_thresholds_met():
    r = evaluate_robust_personal_v2(_good_evidence())
    assert r["pass"] is True and r["verdict"] == "PASS"


def test_robust_fail_on_each_threshold():
    cases = {
        "pbo": {"pbo": 0.25},
        "dsr": {"dsr": 0.90},
        "mintrl": {"min_track_record_coverage": 0},
        "windows": {"outer_test_windows": 4},
        "positive_ratio": {"positive_test_ratio": 0.50},
        "oos_negative": {"oos_net_total": -100},
        "baseline": {"oos_net_total": 700_000, "baseline_net_total": 800_000},
        "cost2x": {"oos_net_2x": -50},
        "neighborhood": {"neighborhood_positive_ratio": 0.50},
        "hashes": {"hashes_valid": False},
        "preregistered": {"preregistered": False},
    }
    for key, over in cases.items():
        r = evaluate_robust_personal_v2(_good_evidence(**over))
        assert r["pass"] is False, f"{key} 应 FAIL"


def test_strict_profile_stricter_than_robust():
    """PBO=0.15 通过 robust 但 strict 要求 <0.10 → FAIL。"""
    robust = evaluate_robust_personal_v2(_good_evidence(pbo=0.15))
    strict = evaluate_strict_research_v2(_good_evidence(pbo=0.15))
    assert robust["pass"] is True
    assert strict["pass"] is False
    # strict 达标：PBO<10%、DSR>95%
    strict_ok = evaluate_strict_research_v2(_good_evidence(pbo=0.05, dsr=0.98))
    assert strict_ok["pass"] is True


def test_promotion_decision_candidate_only():
    """PASS → CANDIDATE；不得写 A 池或订单（note 声明）。"""
    decision = promotion_decision(_good_evidence())
    assert decision["candidate"] == "CANDIDATE"
    assert "不得写 A 池或订单" in decision["note"]
    fail = promotion_decision(_good_evidence(pbo=0.30))
    assert fail["candidate"] == "NO_CANDIDATE"
    assert ROBUST_PROFILE in decision["profiles"] and STRICT_PROFILE in decision["profiles"]
