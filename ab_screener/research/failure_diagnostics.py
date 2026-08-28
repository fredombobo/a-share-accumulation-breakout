"""Deterministic diagnostics for a failed authoritative research gate."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from ab_screener.research.deflated_sharpe import (
    deflated_sharpe,
    expected_max_sharpe_null,
)
from ab_screener.research.formal_evidence import statistical_formal_evidence

RESEARCH_FAILURE_DIAGNOSTIC_VERSION = "research-failure-diagnostic-v1.0.0"


def diagnose_research_failure(
    report: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    """Explain whether a failed gate is multiplicity, sample length, or edge stability."""
    formal_returns = checkpoint.get("formal_is_returns")
    if not isinstance(formal_returns, Mapping):
        return _seal(
            {
                "version": RESEARCH_FAILURE_DIAGNOSTIC_VERSION,
                "research_run_id": report.get("research_run_id"),
                "status": "INSUFFICIENT",
                "reason": "检查点缺少 formal_is_returns，不能复算独立试验与嵌套窗",
            }
        )
    recalculated = statistical_formal_evidence(formal_returns)
    matrix = _mapping(recalculated.get("return_matrix"))
    if matrix.get("status") != "OK":
        return _seal(
            {
                "version": RESEARCH_FAILURE_DIAGNOSTIC_VERSION,
                "research_run_id": report.get("research_run_id"),
                "status": "INSUFFICIENT",
                "reason": matrix.get("reason"),
                "return_matrix": matrix,
            }
        )

    is_rows = {
        str(row.get("param_id")): row
        for row in _rows(checkpoint.get("is_all"))
        if row.get("param_id") is not None
    }
    groups = _rows(matrix.get("parameter_groups"))
    representative_rows = [
        is_rows.get(str(group.get("representative_param_id"))) for group in groups
    ]
    independent_returns = [
        value
        for row in representative_rows
        if row is not None
        and (value := _number(row.get("net_total_return", row.get("net_avg_return")))) is not None
    ]
    profitable_count = sum(value > 0 for value in independent_returns)
    profitable_ratio = (
        profitable_count / len(independent_returns) if independent_returns else None
    )

    recalculated_nested = _mapping(recalculated.get("nested_walkforward"))
    nested_windows = _rows(recalculated_nested.get("windows"))
    dates = _aligned_dates(formal_returns)
    dated_windows = [_window_with_dates(row, dates) for row in nested_windows]
    positive_test_ratio = _number(recalculated_nested.get("positive_test_ratio"))
    negative_selected_train_windows = sum(
        (_number(row.get("selected_train_return")) or 0.0) <= 0 for row in nested_windows
    )

    reported_stats = _mapping(report.get("v2_statistics"))
    adjusted_dsr = _effective_dsr(reported_stats, matrix)
    pbo = _number(_mapping(recalculated.get("cscv_pbo")).get("pbo"))
    mintrl_coverage = _number(reported_stats.get("min_track_record_coverage"))
    classification = _classification(
        profitable_ratio=profitable_ratio,
        positive_test_ratio=positive_test_ratio,
        duplicate_count=int(matrix.get("exact_duplicate_parameters") or 0),
    )
    return _seal(
        {
            "version": RESEARCH_FAILURE_DIAGNOSTIC_VERSION,
            "research_run_id": report.get("research_run_id"),
            "status": "OK",
            "classification": classification,
            "return_matrix": matrix,
            "independent_is_paths": {
                "evaluated": len(independent_returns),
                "profitable": profitable_count,
                "profitable_ratio": _rounded(profitable_ratio),
            },
            "corrected_statistics": {
                "pbo": pbo,
                "dsr_effective_trials": adjusted_dsr,
                "min_track_record_coverage": mintrl_coverage,
                "nested_positive_test_ratio": positive_test_ratio,
                "nested_positive_windows": sum(
                    (_number(row.get("test_return")) or 0.0) > 0 for row in nested_windows
                ),
                "nested_windows": len(nested_windows),
                "nested_selected_train_nonpositive": negative_selected_train_windows,
            },
            "threshold_gaps": {
                "pbo_excess_over_0_20": _gap_above(pbo, 0.20),
                "dsr_shortfall_to_0_95": _gap_below(adjusted_dsr, 0.95),
                "mintrl_shortfall_to_1_00": _gap_below(mintrl_coverage, 1.0),
                "nested_shortfall_to_0_60": _gap_below(positive_test_ratio, 0.60),
            },
            "nested_windows": dated_windows,
            "interpretation": _interpretation(classification),
        }
    )


def _effective_dsr(stats: Mapping[str, Any], matrix: Mapping[str, Any]) -> float | None:
    sharpe = _number(stats.get("sharpe_period"))
    periods_raw = _number(stats.get("n_periods"))
    skew = _number(stats.get("skew"))
    kurtosis = _number(stats.get("kurtosis"))
    trials_raw = _number(matrix.get("effective_parameters"))
    trial_std = _number(matrix.get("trial_sharpe_std"))
    if any(
        value is None
        for value in (sharpe, periods_raw, skew, kurtosis, trials_raw, trial_std)
    ):
        return None
    assert sharpe is not None
    assert periods_raw is not None
    assert skew is not None
    assert kurtosis is not None
    assert trials_raw is not None
    assert trial_std is not None
    periods = int(periods_raw)
    trials = int(trials_raw)
    try:
        sr0 = expected_max_sharpe_null(trials, sharpe_std=trial_std)
        return round(
            deflated_sharpe(sharpe, periods, skew, kurtosis, trials, sr0=sr0),
            6,
        )
    except ValueError:
        return None


def _classification(
    *,
    profitable_ratio: float | None,
    positive_test_ratio: float | None,
    duplicate_count: int,
) -> str:
    if profitable_ratio is not None and profitable_ratio == 0:
        return "TEMPORAL_EDGE_INSTABILITY"
    if positive_test_ratio is not None and positive_test_ratio < 0.60:
        return "TEMPORAL_EDGE_INSTABILITY"
    if duplicate_count > 0:
        return "TRIAL_MULTIPLICITY_INFLATION"
    return "EVIDENCE_LENGTH_OR_SIGNIFICANCE"


def _interpretation(classification: str) -> str:
    if classification == "TEMPORAL_EDGE_INSTABILITY":
        return (
            "主要失败来自训练期收益路径不盈利或跨窗口符号不稳定；精确重复试验会夸大"
            "多重检验次数，但不是降低正式阈值或宣称策略有效的理由。"
        )
    if classification == "TRIAL_MULTIPLICITY_INFLATION":
        return "主要可纠正项是精确重复收益路径被重复计数；仍需重新通过全部正式门槛。"
    return "现有证据主要受样本长度或统计显著性约束；需要新增未触碰数据，不能放宽阈值。"


def _aligned_dates(series_by_param: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(date)
            for raw_series in series_by_param.values()
            if isinstance(raw_series, Mapping)
            for date in raw_series
        }
    )


def _window_with_dates(row: dict[str, Any], dates: list[str]) -> dict[str, Any]:
    result = dict(row)
    for prefix in ("train", "test"):
        start = int(row.get(f"{prefix}_start") or 0)
        end = int(row.get(f"{prefix}_end") or 0)
        result[f"{prefix}_start_date"] = dates[start] if 0 <= start < len(dates) else None
        result[f"{prefix}_end_date"] = dates[end - 1] if 0 < end <= len(dates) else None
    return result


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _gap_above(value: float | None, threshold: float) -> float | None:
    return round(max(0.0, value - threshold), 6) if value is not None else None


def _gap_below(value: float | None, threshold: float) -> float | None:
    return round(max(0.0, threshold - value), 6) if value is not None else None


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**payload, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
