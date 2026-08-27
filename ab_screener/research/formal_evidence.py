"""Build auditable formal evidence from frozen portfolio-return series."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from ab_screener.research.cscv import PboError, cscv_pbo
from ab_screener.research.nested_walkforward import (
    NestedWalkforwardError,
    nested_parameter_walkforward,
)

FORMAL_EVIDENCE_VERSION = "formal-evidence-v2.0.0"


class FormalEvidenceError(ValueError):
    """Formal evidence input is incomplete or internally inconsistent."""


def aligned_return_matrix(
    series_by_param: Mapping[str, Mapping[str, float]],
) -> tuple[list[str], list[str], np.ndarray, dict[str, Any]]:
    """Align sparse daily portfolio returns; missing dates mean the account stayed in cash."""
    param_ids = sorted(str(key) for key in series_by_param)
    if len(param_ids) < 4:
        raise FormalEvidenceError("正式统计至少需要 4 组参数收益序列")
    dates = sorted(
        {
            str(date)
            for param_id in param_ids
            for date in _series(series_by_param, param_id)
        }
    )
    if len(dates) < 16:
        raise FormalEvidenceError("正式统计至少需要 16 个对齐收益日")
    date_index = {date: index for index, date in enumerate(dates)}
    matrix: np.ndarray = np.zeros((len(dates), len(param_ids)), dtype="<f8")
    for column, param_id in enumerate(param_ids):
        values = _series(series_by_param, param_id)
        if not values:
            raise FormalEvidenceError(f"参数 {param_id} 缺少收益序列")
        for raw_date, raw_value in values.items():
            date = str(raw_date)
            value = _number(raw_value)
            if value is None or value <= -1.0:
                raise FormalEvidenceError(f"参数 {param_id} 在 {date} 的收益非法")
            matrix[date_index[date], column] = value
    digest = hashlib.sha256()
    digest.update(json.dumps(dates, separators=(",", ":")).encode("ascii"))
    digest.update(json.dumps(param_ids, separators=(",", ":")).encode("ascii"))
    digest.update(matrix.tobytes(order="C"))
    means = matrix.mean(axis=0)
    standard_deviations = matrix.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        trial_sharpes = np.where(standard_deviations > 0, means / standard_deviations, 0.0)
    trial_sharpe_std = float(trial_sharpes.std(ddof=1))
    metadata = {
        "periods": len(dates),
        "parameters": len(param_ids),
        "start": dates[0],
        "end": dates[-1],
        "missing_policy": "cash_zero_return",
        "trial_sharpe_std": round(trial_sharpe_std, 8),
        "sha256": digest.hexdigest(),
    }
    return dates, param_ids, matrix, metadata


def statistical_formal_evidence(
    series_by_param: Mapping[str, Mapping[str, float]],
    *,
    pbo_splits: int = 16,
    nested_windows: int = 5,
) -> dict[str, Any]:
    """Compute CSCV-PBO and true nested parameter selection from one frozen matrix."""
    try:
        _dates, param_ids, matrix, metadata = aligned_return_matrix(series_by_param)
    except FormalEvidenceError as exc:
        reason = str(exc)
        return {
            "return_matrix": {"status": "INSUFFICIENT", "reason": reason},
            "cscv_pbo": {"status": "INSUFFICIENT", "reason": reason},
            "nested_walkforward": {"status": "INSUFFICIENT", "reason": reason},
        }
    result: dict[str, Any] = {"return_matrix": {"status": "OK", **metadata}}
    try:
        result["cscv_pbo"] = {"status": "OK", **cscv_pbo(matrix, n_splits=pbo_splits)}
    except PboError as exc:
        result["cscv_pbo"] = {"status": "INSUFFICIENT", "reason": str(exc)}
    try:
        result["nested_walkforward"] = {
            "status": "OK",
            **nested_parameter_walkforward(
                matrix,
                param_ids,
                n_test_windows=nested_windows,
                train_frac=0.5,
                min_train_periods=40,
            ),
        }
    except NestedWalkforwardError as exc:
        result["nested_walkforward"] = {"status": "INSUFFICIENT", "reason": str(exc)}
    return result


def parameter_neighborhood_evidence(
    oos_rows: list[dict[str, Any]],
    planned_param_ids: list[str],
    *,
    baseline_net_total: float | None,
) -> dict[str, Any]:
    """Score every preregistered one-coordinate neighbor; any missing row fails coverage."""
    planned = sorted({str(value) for value in planned_param_ids if value})
    if len(planned) < 2:
        return {
            "status": "INSUFFICIENT",
            "reason": "预登记参数邻域少于 2 组",
            "planned_param_ids": planned,
            "coverage": 0.0,
            "positive_excess_ratio": None,
        }
    baseline = _number(baseline_net_total)
    if baseline is None:
        return {
            "status": "INSUFFICIENT",
            "reason": "预登记主基线净总收益缺失",
            "planned_param_ids": planned,
            "coverage": 0.0,
            "positive_excess_ratio": None,
        }
    rows = {str(row.get("param_id")): row for row in oos_rows if row.get("param_id")}
    evaluated: list[dict[str, Any]] = []
    missing: list[str] = []
    positive = 0
    for param_id in planned:
        row = rows.get(param_id)
        value = _number(
            row.get("oos_net_total_return", row.get("oos_net_avg_return")) if row else None
        )
        if value is None:
            missing.append(param_id)
            continue
        beat = value > baseline
        positive += int(beat)
        evaluated.append({"param_id": param_id, "net_total_return": value, "beat_baseline": beat})
    coverage = len(evaluated) / len(planned)
    ratio = positive / len(planned)
    return {
        "status": "OK" if not missing else "INCOMPLETE",
        "planned_param_ids": planned,
        "planned_count": len(planned),
        "evaluated_count": len(evaluated),
        "missing_param_ids": missing,
        "coverage": round(coverage, 6),
        "positive_excess_ratio": round(ratio, 6),
        "baseline_net_total": baseline,
        "evaluated": evaluated,
    }


def formal_identity_valid(formal: Mapping[str, Any]) -> bool:
    """Validate immutable identities required by the final promotion gate."""
    matrix = formal.get("return_matrix")
    stress = formal.get("cost_stress")
    return bool(
        formal.get("version") == FORMAL_EVIDENCE_VERSION
        and isinstance(matrix, Mapping)
        and matrix.get("status") == "OK"
        and len(str(matrix.get("sha256") or "")) == 64
        and isinstance(stress, Mapping)
        and stress.get("status") == "OK"
        and int(stress.get("cost_multiplier_bps") or 0) == 20_000
        and len(str(stress.get("portfolio_config_hash") or "")) == 16
    )


def _series(
    series_by_param: Mapping[str, Mapping[str, float]],
    param_id: str,
) -> Mapping[str, float]:
    raw = series_by_param.get(param_id)
    if not isinstance(raw, Mapping):
        raise FormalEvidenceError(f"参数 {param_id} 收益序列不是对象")
    return raw


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
