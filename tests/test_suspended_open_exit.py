"""Suspended scheduled baseline exits retain ownership and retry actual quotes."""
from __future__ import annotations

import pandas as pd
import pytest

from ab_screener.research import baselines
from ab_screener.research.portfolio_accounting import PortfolioAccountingError, simulate_portfolio
from tests.test_execution_corrections_20260913 import bar, full_policy, trade, zero_rules


def suspended_market():
    code = "002058.SZ"
    rows = [bar(code, day) for day in ("20260901", "20260902", "20260903", "20260904")]
    rows[2].update(open=0, high=0, low=0, close=10, vol=0, amount=0)
    rows[3].update(open=10.5, high=10.6, low=10.4, close=10.5)
    return code, pd.DataFrame(rows)


@pytest.mark.parametrize("planned_price", [0, None])
def test_suspended_open_exit_retries_without_erasing_entry(planned_price):
    code, market = suspended_market()
    result = simulate_portfolio([trade(code, price=planned_price, exit_phase="OPEN")], market,
                                policy=full_policy(), rules=zero_rules([code]))
    retry = next(row for row in result["events"] if row["event"] == "EXIT_RETRY")
    sold = next(row for row in result["events"] if row["event"] == "EXIT_FILLED")
    suspended = next(row for row in result["equity_curve"] if row["trade_date"] == "20260903")
    assert retry["trade_date"] == "20260903" and retry["cash_delta_fen"] == 0
    assert suspended["market_value_fen"] == suspended["equity_fen"] == 1_000_000
    assert sold["trade_date"] == "20260904" and sold["price_micro"] == 10_500_000
    assert result["portfolio_n_entries"] == 1 and result["portfolio_open_positions"] == 0
    assert result["portfolio_final_equity_fen"] == 1_050_000


def test_suspension_through_window_end_is_incomplete_and_still_valued():
    code, market = suspended_market()
    result = simulate_portfolio([trade(code, price=0, exit_phase="OPEN")], market.iloc[:3],
                                policy=full_policy(), rules=zero_rules([code]))
    assert result["portfolio_status"] == "INCOMPLETE_OPEN_POSITIONS"
    assert result["portfolio_open_positions"] == 1
    assert result["portfolio_final_equity_fen"] == 1_000_000


@pytest.mark.parametrize("phase,price", [("CLOSE", 0), ("INTRADAY", 0), ("OPEN", -1)])
def test_invalid_nonmarket_reference_still_rejected(phase, price):
    code, market = suspended_market()
    with pytest.raises(PortfolioAccountingError, match="退出参考价非法"):
        simulate_portfolio([trade(code, price=price, exit_phase=phase)], market, policy=full_policy())


@pytest.mark.parametrize("baseline", ["random", "ma"])
def test_real_baseline_keeps_suspended_exit_until_next_open(baseline):
    code = "002058.SZ"
    rows = [bar(code, f"202609{index + 1:02d}") for index in range(8)]
    for row, close in zip(rows, [10, 10, 9, 12, 12, 12, 12, 12]):
        row.update(open=close, high=close + .1, low=close - .1, close=close, pre_close=close)
    rows[5].update(open=0, high=0, low=0, vol=0, amount=0)
    kwargs = {"hold_days": 1, "entry_start": "20260905", "entry_end": "20260906",
              "portfolio_policy": full_policy()}
    if baseline == "random":
        result = baselines.random_baseline_trades(pd.DataFrame(rows), n_trades=1, **kwargs)
    else:
        result = baselines.ma_cross_baseline(pd.DataFrame(rows), fast=2, slow=3, **kwargs)
    assert result["net_n_trades"] == 1
    assert result["portfolio_open_positions"] == 0
    assert result["portfolio_status"] == "PASS"
    assert result["net_total_return"] < 0  # Flat prices still incur real fees.
