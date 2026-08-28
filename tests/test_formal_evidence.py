from __future__ import annotations

import numpy as np
import pytest

from ab_screener.research.formal_evidence import (
    aligned_return_matrix,
    parameter_neighborhood_evidence,
    statistical_formal_evidence,
)
from ab_screener.research.nested_walkforward import nested_parameter_walkforward


def _series() -> dict[str, dict[str, float]]:
    dates = [f"2026{month:02d}{day:02d}" for month in range(1, 9) for day in range(1, 9)]
    return {
        f"p{column}": {
            date: 0.0002 * (column + 1) + (index % 3 - 1) * 0.00005
            for index, date in enumerate(dates)
            if not (column == 3 and index == 0)
        }
        for column in range(4)
    }


def test_return_matrix_aligns_cash_days_and_has_stable_hash() -> None:
    dates, param_ids, matrix, metadata = aligned_return_matrix(_series())
    again = aligned_return_matrix(dict(reversed(list(_series().items()))))[3]

    assert matrix.shape == (64, 4)
    assert param_ids == ["p0", "p1", "p2", "p3"]
    assert matrix[dates.index("20260101"), 3] == 0.0
    assert metadata["sha256"] == again["sha256"]


def test_statistical_evidence_runs_pbo_and_true_nested_parameter_selection() -> None:
    evidence = statistical_formal_evidence(_series(), pbo_splits=8, nested_windows=5)

    assert evidence["return_matrix"]["status"] == "OK"
    assert evidence["cscv_pbo"]["status"] == "OK"
    assert len(evidence["nested_walkforward"]["windows"]) == 5
    assert all("selected_param_id" in row for row in evidence["nested_walkforward"]["windows"])


def test_exact_duplicate_paths_count_once_but_near_duplicate_remains_independent() -> None:
    series = _series()
    series["p4-exact"] = dict(series["p0"])
    series["p5-near"] = dict(series["p0"])
    first_date = min(series["p5-near"])
    series["p5-near"][first_date] += 1e-15

    _dates, param_ids, matrix, metadata = aligned_return_matrix(series)

    assert matrix.shape == (64, 5)
    assert len(param_ids) == 5
    assert metadata["nominal_parameters"] == 6
    assert metadata["effective_parameters"] == 5
    assert metadata["exact_duplicate_parameters"] == 1
    assert metadata["deduplication"]["near_duplicates_collapsed"] is False
    assert metadata["exact_duplicate_groups"] == [
        {
            "representative_param_id": "p0",
            "param_ids": ["p0", "p4-exact"],
            "count": 2,
        }
    ]
    assert len(metadata["sha256"]) == len(metadata["nominal_sha256"]) == 64


def test_fewer_than_four_effective_paths_fails_closed_with_audit_counts() -> None:
    original = _series()["p0"]
    evidence = statistical_formal_evidence(
        {f"duplicate-{index}": dict(original) for index in range(4)}
    )

    matrix = evidence["return_matrix"]
    assert matrix["status"] == "INSUFFICIENT"
    assert matrix["nominal_parameters"] == 4
    assert matrix["effective_parameters"] == 1
    assert evidence["cscv_pbo"]["status"] == "INSUFFICIENT"
    assert evidence["nested_walkforward"]["status"] == "INSUFFICIENT"


def test_nested_selection_cannot_see_future_test_changes() -> None:
    base = np.zeros((120, 4), dtype=float)
    base[:72, 0] = 0.002
    base[:72, 1] = 0.001
    first = nested_parameter_walkforward(base, ["a", "b", "c", "d"], n_test_windows=5)
    changed = base.copy()
    first_test_end = int(first["windows"][0]["test_end"])
    changed[first_test_end:, 3] = 0.50
    second = nested_parameter_walkforward(changed, ["a", "b", "c", "d"], n_test_windows=5)

    assert first["windows"][0] == second["windows"][0]


def test_missing_planned_neighbor_fails_coverage_instead_of_disappearing() -> None:
    rows = [
        {"param_id": "n1", "oos_net_total_return": 0.03},
        {"param_id": "n2", "oos_net_total_return": -0.01},
    ]
    evidence = parameter_neighborhood_evidence(
        rows,
        ["n1", "n2", "n3"],
        baseline_net_total=0.0,
    )

    assert evidence["status"] == "INCOMPLETE"
    assert evidence["coverage"] == pytest.approx(2 / 3, abs=1e-6)
    assert evidence["positive_excess_ratio"] == pytest.approx(1 / 3, abs=1e-6)
    assert evidence["missing_param_ids"] == ["n3"]
