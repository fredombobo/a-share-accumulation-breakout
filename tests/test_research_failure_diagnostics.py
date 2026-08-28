from __future__ import annotations

from ab_screener.research.failure_diagnostics import diagnose_research_failure


def _returns() -> dict[str, dict[str, float]]:
    dates = [f"d{index:03d}" for index in range(64)]
    result = {
        f"p{column}": {
            date: -0.0012 + column * 0.00012 + ((index + column) % 5 - 2) * 0.00008
            for index, date in enumerate(dates)
        }
        for column in range(4)
    }
    result["p4-duplicate"] = dict(result["p0"])
    return result


def test_diagnostic_separates_duplicate_trials_from_temporal_edge_failure() -> None:
    checkpoint = {
        "formal_is_returns": _returns(),
        "is_all": [
            {"param_id": f"p{column}", "net_total_return": -0.10 + column * 0.01}
            for column in range(4)
        ]
        + [{"param_id": "p4-duplicate", "net_total_return": -0.10}],
    }
    report = {
        "research_run_id": "failed-run",
        "v2_statistics": {
            "status": "OK",
            "n_periods": 241,
            "sharpe_period": 0.059854,
            "skew": 0.0315,
            "kurtosis": 5.5327,
            "dsr": 0.68,
            "min_track_record_coverage": 0.318,
        },
    }

    diagnostic = diagnose_research_failure(report, checkpoint)

    assert diagnostic["status"] == "OK"
    assert diagnostic["classification"] == "TEMPORAL_EDGE_INSTABILITY"
    assert diagnostic["return_matrix"]["nominal_parameters"] == 5
    assert diagnostic["return_matrix"]["effective_parameters"] == 4
    assert diagnostic["independent_is_paths"] == {
        "evaluated": 4,
        "profitable": 0,
        "profitable_ratio": 0.0,
    }
    assert isinstance(diagnostic["corrected_statistics"]["dsr_effective_trials"], float)
    assert diagnostic["threshold_gaps"]["mintrl_shortfall_to_1_00"] == 0.682
    assert len(diagnostic["nested_windows"]) == 5
    assert diagnostic["sha256"] == diagnose_research_failure(report, checkpoint)["sha256"]


def test_diagnostic_fails_closed_without_frozen_return_paths() -> None:
    diagnostic = diagnose_research_failure(
        {"research_run_id": "missing"},
        {"is_all": []},
    )

    assert diagnostic["status"] == "INSUFFICIENT"
    assert "formal_is_returns" in diagnostic["reason"]
    assert len(diagnostic["sha256"]) == 64
