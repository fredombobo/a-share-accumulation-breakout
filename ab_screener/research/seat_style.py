"""席位行为风格概率与漂移检测（T06）。研究用，不产生交易指令。"""
from __future__ import annotations

from typing import Any

from ab_screener.features.lhb_features import (
    FEATURE_MODEL_VERSION,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    compute_seat_features,
)

STYLE_MODEL_VERSION = "lhb-style-v1"
STYLE_LABELS = (
    "board_chase",
    "trend_swing",
    "dip_reclaim",
    "day_trade_t",
    "pry_board",
)
DRIFT_MIN_SAMPLE = 8
DRIFT_TV_THRESHOLD = 0.35
PROB_SUM_TOLERANCE = 1e-9


def _nz(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def style_scores(features: dict[str, Any]) -> dict[str, float]:
    first = _nz(features.get("first_board_share"))
    consec = _nz(features.get("consecutive_board_share"))
    trend = _nz(features.get("trend_share"))
    reversal = _nz(features.get("reversal_share"))
    direction = _nz(features.get("direction"))
    purity = _nz(features.get("purity"))
    persistence = _nz(features.get("persistence"))
    turnover = _nz(features.get("turnover_mean"))
    turnover_n = min(turnover / 20.0, 1.0) if turnover else 0.0
    both_sides = 1.0 - purity
    return {
        "board_chase": first * (0.5 + 0.5 * direction) * (0.4 + 0.6 * turnover_n),
        "trend_swing": trend * persistence * (1.0 - first),
        "dip_reclaim": reversal * direction * (1.0 - consec),
        "day_trade_t": both_sides * (1.0 - abs(2.0 * direction - 1.0)),
        "pry_board": consec * (0.3 + 0.7 * (1.0 - direction)),
    }


def normalize_probs(scores: dict[str, float]) -> dict[str, float]:
    vals = [max(0.0, float(scores.get(label, 0.0))) for label in STYLE_LABELS]
    total = sum(vals)
    if total <= 0:
        even = 1.0 / len(STYLE_LABELS)
        return {label: even for label in STYLE_LABELS}
    return {label: val / total for label, val in zip(STYLE_LABELS, vals, strict=True)}


def classify_seat_style(feature_result: dict[str, Any]) -> dict[str, Any]:
    if feature_result.get("status") != STATUS_OK or not feature_result.get("features"):
        return {
            "status": STATUS_INSUFFICIENT,
            "seat_id": feature_result.get("seat_id"),
            "window_days": feature_result.get("window_days"),
            "model_version": STYLE_MODEL_VERSION,
            "feature_model_version": FEATURE_MODEL_VERSION,
            "probs": None,
            "uninformative": False,
        }
    scores = style_scores(feature_result["features"])
    raw_sum = sum(max(0.0, v) for v in scores.values())
    probs = normalize_probs(scores)
    assert abs(sum(probs.values()) - 1.0) <= PROB_SUM_TOLERANCE
    return {
        "status": STATUS_OK,
        "seat_id": feature_result["seat_id"],
        "window_days": feature_result["window_days"],
        "as_of": feature_result["as_of"],
        "model_version": STYLE_MODEL_VERSION,
        "feature_model_version": FEATURE_MODEL_VERSION,
        "probs": probs,
        "uninformative": raw_sum <= 0,
    }


def total_variation(p: dict[str, float], q: dict[str, float]) -> float:
    return 0.5 * sum(abs(p.get(label, 0.0) - q.get(label, 0.0)) for label in STYLE_LABELS)


def detect_style_drift(
    early: dict[str, Any],
    late: dict[str, Any],
    *,
    min_sample: int = DRIFT_MIN_SAMPLE,
    threshold: float = DRIFT_TV_THRESHOLD,
) -> dict[str, Any]:
    n_early = int(early.get("sample_size") or 0)
    n_late = int(late.get("sample_size") or 0)
    if (
        early.get("status") != STATUS_OK
        or late.get("status") != STATUS_OK
        or n_early < min_sample
        or n_late < min_sample
        or not early.get("probs")
        or not late.get("probs")
    ):
        return {
            "status": STATUS_INSUFFICIENT,
            "alarm": False,
            "tv_distance": None,
            "threshold": threshold,
            "model_version": STYLE_MODEL_VERSION,
        }
    tv = total_variation(early["probs"], late["probs"])
    return {
        "status": STATUS_OK,
        "alarm": tv >= threshold,
        "tv_distance": tv,
        "threshold": threshold,
        "model_version": STYLE_MODEL_VERSION,
    }


def classify_from_facts(facts, *, seat_id: str, as_of: str, as_of_date: str, window_days: int) -> dict[str, Any]:
    feat = compute_seat_features(
        facts, seat_id=seat_id, as_of=as_of, as_of_date=as_of_date, window_days=window_days
    )
    style = classify_seat_style(feat)
    style["sample_size"] = feat.get("sample_size")
    style["features_status"] = feat.get("status")
    return style
