from __future__ import annotations

import pandas as pd

from ab_screener.research.pit_reader import ResearchPitSnapshot
from ab_screener.research.portfolio_accounting import PortfolioPolicy
from ab_screener.research.trusted_run import _cost_stress_evidence


def test_cost_stress_replays_candidate_and_baseline_with_exact_two_x_policy(monkeypatch) -> None:
    seen: list[PortfolioPolicy] = []

    def fake_backtest(**kwargs):
        seen.append(kwargs["portfolio_policy"])
        return {
            "portfolio": {
                "portfolio_status": "PASS",
                "portfolio_total_return": 0.04,
                "portfolio_equity_sha256": "a" * 64,
            }
        }

    def fake_baseline(*_args, **kwargs):
        seen.append(kwargs["portfolio_policy"])
        return {
            "portfolio_status": "PASS",
            "net_total_return": -0.01,
            "portfolio_equity_sha256": "b" * 64,
        }

    monkeypatch.setattr("ab_screener.research.backtest_engine.run_single_backtest", fake_backtest)
    monkeypatch.setattr("ab_screener.research.trusted_run.ma_cross_baseline", fake_baseline)
    daily = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": f"2026080{day}",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.0,
                "pre_close": 10.0,
                "vol": 100_000,
                "amount": 1_000_000,
            }
            for day in range(1, 5)
        ]
    )
    snapshot = ResearchPitSnapshot(
        decision_at="2026-08-10T16:00:00+08:00",
        data_start="20250801",
        data_end="20260804",
        universe=("000001.SZ",),
        universe_sha256="c" * 64,
        dataset_fingerprint="d" * 16,
        daily=daily,
    )

    evidence = _cost_stress_evidence(
        primary_is={
            "strategy": "A",
            "vol_ratio_min": 1.5,
            "strong_reset": 3,
            "exit_window": 10,
            "stop_pct": 0.07,
        },
        primary_oos={"oos_net_n_trades": 40},
        primary_baseline="ma20_60",
        windows={"oos_start": "20260801", "oos_end": "20260804"},
        step=10,
        max_codes=1,
        universe=["000001.SZ"],
        db_path="unused.db",
        portfolio_policy=PortfolioPolicy(),
        research_snapshot=snapshot,
    )

    assert evidence["status"] == "OK"
    assert evidence["cost_multiplier_bps"] == 20_000
    assert evidence["candidate_net_total_2x"] == 0.04
    assert evidence["baseline_net_total_2x"] == -0.01
    assert len(seen) == 2
    assert all(policy.cost_multiplier_bps == 20_000 for policy in seen)
    assert seen[0].fingerprint() == seen[1].fingerprint() == evidence["portfolio_config_hash"]
