from __future__ import annotations

import pandas as pd

from ab_screener.research.portfolio_metric_contract import (
    normalize_portfolio_metrics,
    portfolio_total_return,
)
from ab_screener.research.professional_runner import (
    _independent_leaderboard,
    _metric_subset,
    _path_analysis,
    _verdict,
)
from optimizer import _replay_params, param_id


def test_portfolio_return_aliases_are_normalized_without_inventing_zero() -> None:
    assert portfolio_total_return({"net_avg_return": 0.07456158}) == 0.07456158
    assert portfolio_total_return({"net_total_return": 0.04856007}) == 0.04856007
    assert portfolio_total_return({"portfolio_total_return": -0.01}) == -0.01
    assert portfolio_total_return({"net_avg_return": None}) is None

    normalized = normalize_portfolio_metrics({"net_avg_return": 0.12})
    assert normalized["portfolio_total_return"] == 0.12
    assert normalized["net_total_return"] == 0.12
    assert normalized["net_avg_return"] == 0.12


def test_professional_verdict_uses_compatibility_aliases_consistently() -> None:
    best = {"oos": {"net_n_trades": 79, "net_avg_return": 0.07456158}}
    baselines = {
        "random": {"net_total_return": -0.00086531},
        "ma20_60": {"net_avg_return": -0.05260365},
    }
    stress = {"metrics": {"net_total_return": 0.04856007}}

    verdict, _, reasons = _verdict(
        best,
        {"evidence_complete": False, "wf_pass": False},
        baselines,
        stress,
    )

    assert verdict == "EXPLORATORY_WEAK"
    assert reasons == ["滚动窗口证据不完整"]

    promising, _, promising_reasons = _verdict(
        best,
        {"evidence_complete": True, "wf_pass": True},
        baselines,
        stress,
    )
    assert promising == "EXPLORATORY_PROMISING"
    assert len(promising_reasons) == 1


def test_metric_subset_materializes_canonical_portfolio_return() -> None:
    metrics = _metric_subset(
        {
            "net_n_trades": 40,
            "net_avg_return": 0.08,
            "portfolio_status": "PASS",
        }
    )

    assert metrics["portfolio_total_return"] == 0.08
    assert metrics["net_total_return"] == 0.08
    assert metrics["evidence_complete"] is True


def test_path_analysis_counts_only_exact_equity_hashes() -> None:
    rows = [
        {
            "param_id": "p1",
            "is": {"portfolio_equity_sha256": "is-a"},
            "oos": {"portfolio_equity_sha256": "oos-a"},
        },
        {
            "param_id": "p2",
            "is": {"portfolio_equity_sha256": "is-a"},
            "oos": {"portfolio_equity_sha256": "oos-a"},
        },
        {
            "param_id": "p3",
            "is": {"portfolio_equity_sha256": "is-b"},
            "oos": {"portfolio_equity_sha256": "oos-b"},
        },
    ]

    result = _path_analysis(rows)

    assert result == {
        "method": "combined_portfolio_equity_sha256",
        "evidence_complete": True,
        "coverage_complete": True,
        "nominal_combinations": 3,
        "path_eligible_combinations": 3,
        "excluded_without_complete_path": 0,
        "independent_is_paths": 2,
        "independent_oos_paths": 2,
        "independent_joint_paths": 2,
        "duplicate_group_count": 1,
    }
    partial = _path_analysis([rows[0], {"param_id": "legacy", "is": {}, "oos": {}}])
    assert partial["evidence_complete"] is True
    assert partial["coverage_complete"] is False
    assert partial["path_eligible_combinations"] == 1
    assert partial["excluded_without_complete_path"] == 1
    assert partial["independent_joint_paths"] == 1

    incomplete = _path_analysis([{"param_id": "legacy", "is": {}, "oos": {}}])
    assert incomplete["evidence_complete"] is False
    assert incomplete["path_eligible_combinations"] == 0
    assert incomplete["excluded_without_complete_path"] == 1
    assert incomplete["independent_joint_paths"] is None


def test_independent_leaderboard_collapses_only_exact_equity_paths() -> None:
    rows = [
        {
            "param_id": "best",
            "is": {"portfolio_equity_sha256": "is-a"},
            "oos": {"portfolio_equity_sha256": "oos-a"},
        },
        {
            "param_id": "equivalent",
            "is": {"portfolio_equity_sha256": "is-a"},
            "oos": {"portfolio_equity_sha256": "oos-a"},
        },
        {
            "param_id": "different-oos",
            "is": {"portfolio_equity_sha256": "is-a"},
            "oos": {"portfolio_equity_sha256": "oos-b"},
        },
        {"param_id": "legacy", "is": {}, "oos": {}},
    ]

    result = _independent_leaderboard(rows)

    assert [row["param_id"] for row in result] == [
        "best",
        "different-oos",
    ]
    assert [row["equivalent_parameter_count"] for row in result] == [2, 1]


def test_true_max_hold_parameter_changes_time_exit_path() -> None:
    bars = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}" for day in range(1, 11)],
            "ts_code": ["000001.SZ"] * 10,
            "open": [10 + day * 0.1 for day in range(10)],
            "high": [10.2 + day * 0.1 for day in range(10)],
            "low": [9.9 + day * 0.1 for day in range(10)],
            "close": [10.1 + day * 0.1 for day in range(10)],
            "vol": [100.0] * 10,
            "pct_chg": [1.0] * 10,
        }
    )
    signals = [
        {
            "day": "20260101",
            "entry_i": 0,
            "bench_vols": {1.5: 1_000.0},
        }
    ]
    short = {
        "strategy": "A",
        "vol_ratio_min": 1.5,
        "strong_reset": 3,
        "exit_window": 10,
        "max_hold_days": 3,
        "stop_pct": 0.5,
        "target_pct": 1.0,
    }
    long = {**short, "max_hold_days": 6}

    result = _replay_params(bars, signals, [short, long])
    short_trade = result[param_id("A", short)][0]
    long_trade = result[param_id("A", long)][0]

    assert short_trade["exit"] == long_trade["exit"] == "time"
    assert short_trade["days"] == 3
    assert long_trade["days"] == 6
    assert short_trade["exit_date"] != long_trade["exit_date"]
