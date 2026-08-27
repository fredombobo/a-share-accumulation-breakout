from __future__ import annotations

from ab_screener.research.validation import evaluate_personal_anti_overfit, evaluate_trusted_gate


def _oos(**overrides: float) -> dict:
    row = {
        "oos_net_n_trades": 40,
        "oos_net_profit_factor": 1.20,
        "oos_net_win_rate": 0.40,
        "oos_net_max_drawdown": 0.20,
        "oos_net_avg_return": 0.012,
    }
    row.update(overrides)
    return row


def _wf() -> list[dict]:
    return [
        {"window": f"WF{i}", "train_pf": 1.20, "test_pf": 1.05, "test_dd": 0.20, "test_n": 35}
        for i in range(1, 4)
    ]


def _baselines() -> dict:
    return {
        "random": {"net_avg_return": 0.001},
        "ma20_60": {"net_avg_return": 0.002},
    }


def _anti_overfit(**overrides) -> dict:
    result = {
        "verdict": "PASS",
        "checks": [
            {
                "id": "anti_trials",
                "label": "参数试验数量",
                "passed": True,
                "actual": 54,
                "threshold": ">= 30",
            },
        ],
        "block_reasons": [],
        "version": "personal-anti-overfit-v1",
    }
    result.update(overrides)
    return result


def test_full_net_oos_wf_and_both_baselines_can_pass() -> None:
    result = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=_oos(),
        wf_windows=_wf(),
        baselines=_baselines(),
        anti_overfit=_anti_overfit(),
    )

    assert result["verdict"] == "PASS"
    assert result["candidate_eligible"] is True
    assert all(check["passed"] for check in result["checks"])


def test_bound_portfolio_model_requires_matching_complete_accounting() -> None:
    model = {"version": "research-portfolio-v2.0.0", "config_hash": "abc123"}
    oos = {
        **_oos(),
        "oos_portfolio_model_version": model["version"],
        "oos_portfolio_config_hash": model["config_hash"],
        "oos_portfolio_status": "PASS",
    }
    wf = [
        {
            **row,
            "train_portfolio_status": "PASS",
            "test_portfolio_status": "PASS",
        }
        for row in _wf()
    ]
    baselines = {
        key: {
            **row,
            "portfolio_model_version": model["version"],
            "portfolio_config_hash": model["config_hash"],
            "portfolio_status": "PASS",
        }
        for key, row in _baselines().items()
    }

    passed = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=oos,
        wf_windows=wf,
        baselines=baselines,
        anti_overfit=_anti_overfit(),
        portfolio_model=model,
    )
    assert passed["verdict"] == "PASS"

    oos["oos_portfolio_status"] = "INCOMPLETE_OPEN_POSITIONS"
    failed = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=oos,
        wf_windows=wf,
        baselines=baselines,
        anti_overfit=_anti_overfit(),
        portfolio_model=model,
    )
    assert failed["verdict"] == "FAIL"
    assert failed["candidate_eligible"] is False


def test_bound_portfolio_model_missing_evidence_is_insufficient() -> None:
    result = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=_oos(),
        wf_windows=_wf(),
        baselines=_baselines(),
        anti_overfit=_anti_overfit(),
        portfolio_model={"version": "research-portfolio-v2.0.0", "config_hash": "abc123"},
    )

    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_manual_or_single_runs_are_insufficient_evidence() -> None:
    manual = evaluate_trusted_gate(
        research_mode="manual",
        automatic_window=False,
        run_mode="grid",
        oos=_oos(),
        wf_windows=_wf(),
        baselines=_baselines(),
        anti_overfit=_anti_overfit(),
    )
    single = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="single",
        oos=_oos(),
        wf_windows=_wf(),
        baselines=_baselines(),
        anti_overfit=_anti_overfit(),
    )

    assert manual["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert single["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_missing_wf_value_never_defaults_to_zero_or_passes() -> None:
    windows = _wf()
    windows[1]["test_dd"] = None

    result = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=_oos(),
        wf_windows=windows,
        baselines=_baselines(),
        anti_overfit=_anti_overfit(),
    )

    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert result["candidate_eligible"] is False


def test_candidate_must_beat_random_and_ma_baselines() -> None:
    baselines = _baselines()
    baselines["ma20_60"]["net_avg_return"] = 0.013

    result = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=_oos(),
        wf_windows=_wf(),
        baselines=baselines,
        anti_overfit=_anti_overfit(),
    )

    assert result["verdict"] == "FAIL"
    assert "MA20/60" in " ".join(result["block_reasons"])


def test_oos_trade_and_drawdown_boundaries_are_enforced() -> None:
    result = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=_oos(oos_net_n_trades=29, oos_net_max_drawdown=0.251),
        wf_windows=_wf(),
        baselines=_baselines(),
        anti_overfit=_anti_overfit(),
    )

    assert result["verdict"] == "FAIL"
    assert result["candidate_eligible"] is False


def test_missing_anti_overfit_evidence_is_insufficient() -> None:
    result = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=_oos(),
        wf_windows=_wf(),
        baselines=_baselines(),
        anti_overfit=None,
    )

    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert result["candidate_eligible"] is False


def test_failed_anti_overfit_gate_keeps_candidate_fail_closed() -> None:
    result = evaluate_trusted_gate(
        research_mode="full",
        automatic_window=True,
        run_mode="grid",
        oos=_oos(),
        wf_windows=_wf(),
        baselines=_baselines(),
        anti_overfit=_anti_overfit(
            verdict="FAIL",
            checks=[
                {
                    "id": "anti_retention",
                    "label": "OOS PF保持率",
                    "passed": False,
                    "actual": 0.4,
                    "threshold": ">= 0.75",
                }
            ],
            block_reasons=["OOS PF保持率不足"],
        ),
    )

    assert result["verdict"] == "FAIL"
    assert "OOS PF保持率不足" in result["block_reasons"]


def test_personal_anti_overfit_passes_only_with_stable_frozen_winner() -> None:
    is_rows = [{"param_id": f"p{i}", "net_profit_factor": 1.4 - i * 0.002} for i in range(54)]
    oos_rows = [
        {"param_id": "p0", "oos_net_profit_factor": 1.15, "oos_net_avg_return": 0.012},
        {"param_id": "p1", "oos_net_profit_factor": 1.08, "oos_net_avg_return": 0.009},
        {"param_id": "p2", "oos_net_profit_factor": 1.02, "oos_net_avg_return": 0.006},
    ]

    result = evaluate_personal_anti_overfit(
        is_candidates=is_rows,
        oos_candidates=oos_rows,
        wf_windows=_wf(),
    )

    assert result["verdict"] == "PASS"
    assert result["version"] == "personal-anti-overfit-v1"


def test_personal_anti_overfit_fails_rank_inversion() -> None:
    is_rows = [{"param_id": f"p{i}", "net_profit_factor": 1.4 - i * 0.002} for i in range(54)]
    oos_rows = [
        {"param_id": "p0", "oos_net_profit_factor": 1.05, "oos_net_avg_return": 0.001},
        {"param_id": "p1", "oos_net_profit_factor": 1.20, "oos_net_avg_return": 0.012},
        {"param_id": "p2", "oos_net_profit_factor": 1.15, "oos_net_avg_return": 0.009},
    ]

    result = evaluate_personal_anti_overfit(
        is_candidates=is_rows,
        oos_candidates=oos_rows,
        wf_windows=_wf(),
    )

    assert result["verdict"] == "FAIL"
    assert any(check["id"] == "anti_oos_rank" and not check["passed"] for check in result["checks"])


def test_personal_anti_overfit_missing_trials_is_insufficient() -> None:
    result = evaluate_personal_anti_overfit(
        is_candidates=[],
        oos_candidates=[],
        wf_windows=[],
    )

    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
