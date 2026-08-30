from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from ab_screener.domain.costs import NOTIONAL, FillResult, summarize_fills
from ab_screener.research.baselines import random_baseline_trades
from ab_screener.research.portfolio_accounting import PortfolioPolicy
from walkforward import eval_combo, predeclared_parameter_neighborhood, run_is_oos, wf_recheck


def _fill(net_return: float) -> FillResult:
    return FillResult(
        filled=True,
        qty=100,
        price=10.0,
        commission=5.0,
        stamp_tax=1.0,
        other_fee=1.0,
        slippage_cost=2.0,
        gross_pnl=net_return * NOTIONAL + 7.0,
        net_pnl=net_return * NOTIONAL,
    )


def test_baseline_summary_exposes_same_net_metrics_as_candidate() -> None:
    summary = summarize_fills([_fill(0.02), _fill(-0.01), _fill(0.01)])

    assert summary["net_avg_return"] == 0.006667
    assert summary["net_win_rate"] == 0.6667
    assert summary["net_profit_factor"] == 3.0
    assert summary["net_max_drawdown"] > 0


def test_random_baseline_uses_same_versioned_portfolio_accounting() -> None:
    daily = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": f"2026080{day}",
                "open": 10 + day,
                "high": 11 + day,
                "low": 9 + day,
                "close": 10.5 + day,
                "pre_close": 9.5 + day,
                "vol": 100_000,
                "amount": 1_000_000,
            }
            for day in range(1, 6)
        ]
    )
    policy = PortfolioPolicy()

    result = random_baseline_trades(
        daily,
        n_trades=1,
        hold_days=1,
        codes=["000001.SZ"],
        portfolio_policy=policy,
    )

    assert result["portfolio_model_version"] == policy.version
    assert result["portfolio_config_hash"] == policy.fingerprint()
    assert result["portfolio_status"] == "PASS"
    assert result["net_avg_return"] == result["net_total_return"]
    assert "trade_net_avg_return" in result


def test_wf_missing_metric_is_incomplete_and_cannot_pass() -> None:
    combo = {
        "strategy": "A",
        "vol_ratio_min": 1.5,
        "strong_reset": 3,
        "exit_window": 10,
        "stop_pct": 0.07,
    }
    complete = {
        "net_profit_factor": 1.2,
        "net_max_drawdown": 0.20,
        "net_win_rate": 0.4,
        "net_n_trades": 35,
    }
    missing = {**complete, "net_max_drawdown": None}
    values = [complete, complete, complete, missing, complete, complete]
    events: list[tuple[str, int]] = []

    with patch("walkforward.eval_combo", side_effect=values):
        result = wf_recheck(
            [combo],
            windows=[("1", "2", "3", "4")] * 3,
            progress_cb=lambda message, progress: events.append((message, progress)),
        )

    row = result.iloc[0]
    assert not bool(row["wf_pass"])
    assert not bool(row["evidence_complete"])
    assert events[0] == ("WF1 训练窗", 0)
    assert events[-1] == ("WF3 完成", 100)


def test_wf_single_combo_preserves_optional_target_percentage() -> None:
    captured: dict = {}

    def fake_grid(*_args, grid=None, **_kwargs):
        captured.update(grid or {})
        return pd.DataFrame([{"net_n_trades": 40, "net_profit_factor": 1.2}])

    with patch("walkforward.run_grid", side_effect=fake_grid):
        eval_combo(
            {
                "strategy": "A",
                "vol_ratio_min": 1.5,
                "strong_reset": 3,
                "exit_window": 10,
                "stop_pct": 0.07,
                "target_pct": 0.15,
            },
            "20250101",
            "20251231",
        )

    assert captured["target_pct"] == [0.15]


def test_is_oos_progress_is_monotonic_and_identifies_oos_phase() -> None:
    row = {
        "strategy": "A",
        "vol_ratio_min": 1.5,
        "strong_reset": 3,
        "exit_window": 10,
        "stop_pct": 0.07,
        "net_n_trades": 40,
        "net_win_rate": 0.4,
        "net_profit_factor": 1.2,
        "net_max_drawdown": 0.2,
        "n_trades": 40,
        "win_rate": 0.4,
        "profit_factor": 1.2,
        "max_drawdown": 0.2,
    }
    events: list[tuple[str, int]] = []

    def fake_grid(*_args, progress_cb=None, **_kwargs):
        if progress_cb:
            progress_cb("开始", 5)
            progress_cb("完成", 100)
        return pd.DataFrame([row])

    with patch("walkforward.run_grid", side_effect=fake_grid):
        run_is_oos("A", top_n=1, progress_cb=lambda message, progress: events.append((message, progress)))

    progresses = [event[1] for event in events]
    assert progresses == sorted(progresses)
    assert any(message.startswith("OOS") for message, _ in events)


def test_parameter_neighborhood_is_fixed_by_is_grid_before_oos() -> None:
    primary = {
        "strategy": "A",
        "vol_ratio_min": 1.3,
        "strong_reset": 2,
        "exit_window": 7,
        "stop_pct": 0.05,
    }
    grid = {
        "vol_ratio_min": [1.3, 1.5],
        "strong_reset": [2, 3],
        "exit_window": [7, 10],
        "stop_pct": [0.05, 0.07],
    }

    neighbors = predeclared_parameter_neighborhood(primary, grid)

    assert len(neighbors) == 4
    for row in neighbors:
        changed = sum(row[key] != primary[key] for key in grid)
        assert changed == 1
