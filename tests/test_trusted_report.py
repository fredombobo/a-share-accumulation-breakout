from __future__ import annotations

import pandas as pd

from ab_screener.research.reporting import freeze_is_winner, render_trusted_report
from ab_screener.research.trusted_run import execute_trusted_research, trusted_portfolio_identity
from local_store import LocalStore


def test_is_rank_one_is_frozen_before_oos_and_not_replaced_by_better_oos() -> None:
    is_rows = [
        {"param_id": "p1", "net_profit_factor": 1.3},
        {"param_id": "p2", "net_profit_factor": 1.2},
    ]
    oos_rows = [
        {"param_id": "p1", "oos_net_profit_factor": 0.9},
        {"param_id": "p2", "oos_net_profit_factor": 2.0},
    ]

    frozen = freeze_is_winner(is_rows, oos_rows)

    assert frozen["primary_is"]["param_id"] == "p1"
    assert frozen["primary_oos"]["param_id"] == "p1"
    assert frozen["sensitivity"][0]["param_id"] == "p2"


def test_markdown_report_contains_reproducibility_and_all_gate_sections() -> None:
    report = {
        "research_run_id": "run-1",
        "verdict": "FAIL",
        "summary": "OOS 净最大回撤未通过",
        "block_reasons": ["OOS 净最大回撤未通过"],
        "versions": {"dataset": "d1", "code": "c1", "cost": "k1"},
        "sample": {"universe_size": 200, "windows": {"is_start": "2023", "oos_end": "2026"}},
        "cost_assumptions": {"notional": 100000, "slippage": 0.001},
        "primary_is": {"param_id": "p1", "net_profit_factor": 1.2},
        "primary_oos": {"param_id": "p1", "oos_net_profit_factor": 1.1},
        "wf_windows": [{"window": "WF1", "test_pf": 1.0}],
        "baselines": {"random": {"net_avg_return": 0.0}, "ma20_60": {"net_avg_return": 0.001}},
        "checks": [{"label": "OOS 净最大回撤", "passed": False, "actual": 0.3, "threshold": "<=25%"}],
        "sensitivity": [{"param_id": "p2"}],
    }

    markdown = render_trusted_report(report)

    for heading in (
        "结论",
        "样本与版本",
        "成本口径",
        "IS / OOS",
        "Walk-forward",
        "基线对照",
        "反过拟合",
        "门禁检查",
        "敏感性",
    ):
        assert heading in markdown
    assert "不会自动进入 A 池或生成订单" in markdown


def test_orchestrator_runs_all_stages_but_missing_formal_evidence_fails_closed(monkeypatch, tmp_path) -> None:
    db = tmp_path / "flow.db"
    LocalStore(db)
    portfolio_identity = trusted_portfolio_identity()
    combo = {
        "param_id": "p1",
        "strategy": "A",
        "vol_ratio_min": 1.5,
        "strong_reset": 3,
        "exit_window": 10,
        "stop_pct": 0.07,
        "net_n_trades": 40,
        "net_profit_factor": 1.4,
        "net_win_rate": 0.4,
        "net_max_drawdown": 0.2,
    }
    oos = {
        "param_id": "p1",
        **{
            key: combo[key]
            for key in ("strategy", "vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
        },
        "oos_net_n_trades": 40,
        "oos_net_profit_factor": 1.2,
        "oos_net_win_rate": 0.4,
        "oos_net_max_drawdown": 0.2,
        "oos_net_avg_return": 0.012,
        "oos_portfolio_model_version": portfolio_identity["version"],
        "oos_portfolio_config_hash": portfolio_identity["config_hash"],
        "oos_portfolio_execution_model_version": portfolio_identity["execution_model_version"],
        "oos_portfolio_fee_version": portfolio_identity["fee_version"],
        "oos_portfolio_status": "PASS",
    }
    monkeypatch.setattr("optimizer.research_universe", lambda _limit, **kwargs: ["000001.SZ"])
    monkeypatch.setattr(
        "walkforward.run_is_oos",
        lambda **_kwargs: {
            "is": pd.DataFrame(
                [
                    combo,
                    *[
                        {**combo, "param_id": f"p{i}", "net_profit_factor": 1.39 - i * 0.002}
                        for i in range(2, 55)
                    ],
                ]
            ),
            "oos": pd.DataFrame(
                [
                    oos,
                    {**oos, "param_id": "p2", "oos_net_profit_factor": 1.1, "oos_net_avg_return": 0.009},
                    {**oos, "param_id": "p3", "oos_net_profit_factor": 1.05, "oos_net_avg_return": 0.006},
                ]
            ),
            "msg": None,
        },
    )
    wf_detail = [
        {
            "window": f"WF{i}",
            "train_pf": 1.2,
            "test_pf": 1.05,
            "test_dd": 0.2,
            "test_n": 35,
            "train_portfolio_status": "PASS",
            "test_portfolio_status": "PASS",
        }
        for i in range(1, 4)
    ]
    monkeypatch.setattr(
        "walkforward.wf_recheck",
        lambda *_args, **_kwargs: pd.DataFrame([{"wf_detail": wf_detail}]),
    )
    baseline = {
        "n_trades": 40,
        "net_avg_return": 0.001,
        "net_profit_factor": 1.0,
        "net_max_drawdown": 0.1,
        "portfolio_model_version": portfolio_identity["version"],
        "portfolio_config_hash": portfolio_identity["config_hash"],
        "portfolio_execution_model_version": portfolio_identity["execution_model_version"],
        "portfolio_fee_version": portfolio_identity["fee_version"],
        "portfolio_status": "PASS",
    }
    monkeypatch.setattr("ab_screener.research.trusted_run.random_baseline_trades", lambda *_a, **_k: baseline)
    monkeypatch.setattr("ab_screener.research.trusted_run.ma_cross_baseline", lambda *_a, **_k: baseline)
    phases: list[str] = []
    windows = {
        "is_start": "20230801",
        "is_end": "20240731",
        "oos_start": "20240801",
        "oos_end": "20260731",
        "mode": "full",
        "automatic_window": True,
        "wf_windows": [{"train_start": "1", "train_end": "2", "test_start": "3", "test_end": "4"}] * 3,
    }

    result = execute_trusted_research(
        research_run_id="flow",
        request={
            "strategy": "A",
            "mode": "grid",
            "max_codes": 200,
            "portfolio_model": portfolio_identity,
        },
        windows=windows,
        db_path=db,
        code_version="c",
        dataset_version="d",
        phase_cb=lambda phase, _pct, _message, _state: phases.append(phase),
    )

    assert result["trusted_report"]["traditional_gate"]["verdict"] == "PASS"
    assert result["trusted_report"]["verdict"] == "FAIL"
    assert result["trusted_report"]["candidate_eligible"] is False
    assert result["trusted_report"]["formal_promotion"]["candidate"] == "NO_CANDIDATE"
    assert result["trusted_report"]["anti_overfit"]["verdict"] == "PASS"
    assert {"IS", "OOS", "WF", "BASELINES", "GATE", "REPORT", "CANDIDATE"} <= set(phases)
