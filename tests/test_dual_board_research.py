from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from ab_screener.domain.costs import simulate_round_trip
from ab_screener.domain.execution.market_rules import can_trade, limit_prices_micro
from ab_screener.domain.execution.models import Quote
from ab_screener.domain.stock_board_rules import cap_research_buy, ordinary_limit_bps, research_quantity_step
from ab_screener.research.cost_adjustment import cost_adjusted_trade
from ab_screener.research.portfolio_accounting import (
    PortfolioPolicy,
    resolved_stock_rules,
    simulate_portfolio,
)
from scripts.run_dual_board_pair import select_dual_board, write_once


@pytest.mark.parametrize("code,day,bps", [
    ("688001.SH", "20240902", 2000), ("300001.SZ", "20200824", 2000),
    ("300001.SZ", "20200821", 1000), ("301001.SZ", "2026-09-04", 2000),
    ("600001.SH", "20240902", 1000), ("000001.SZ", "20240902", 1000),
])
def test_ordinary_board_rule_is_date_aware(code, day, bps):
    assert ordinary_limit_bps(code, day) == bps


@pytest.mark.parametrize("day", ["", "20261301", "wrong"])
def test_missing_dual_board_date_fails_closed(day):
    with pytest.raises(ValueError):
        ordinary_limit_bps("300001.SZ", day)


@pytest.mark.parametrize("code", ["688001.SH", "300001.SZ", "301001.SZ"])
def test_dual_board_one_side_limits_and_ten_percent_not_limit(code):
    q = Quote(code, "20240902", 12_000_000, 12_000_000, 12_000_000, 12_000_000,
              1_000_000, 1_000_000, pre_close_micro=10_000_000)
    assert limit_prices_micro(q) == (12_000_000, 8_000_000)
    assert can_trade(q, "BUY") == (False, "LIMIT_UP_ONE_SIDE")
    down = replace(q, open_micro=8_000_000, high_micro=8_000_000, low_micro=8_000_000, close_micro=8_000_000)
    assert can_trade(down, "SELL") == (False, "LIMIT_DOWN_ONE_SIDE")
    ten = replace(q, open_micro=11_000_000, high_micro=11_000_000, low_micro=11_000_000, close_micro=11_000_000)
    assert can_trade(ten, "BUY") == (True, "")


def test_star_sizing_is_conservative_and_explicit():
    assert research_quantity_step("688001.SH") == 200
    assert research_quantity_step("688001.SH", 300) == 600
    assert research_quantity_step("300001.SZ") == 100
    assert cap_research_buy("688001.SH", 100_000, 200) == 50_000
    assert cap_research_buy("688001.SH", 199, 200) == 0
    with pytest.raises(ValueError):
        research_quantity_step("688001.SH", 0)
    rules = resolved_stock_rules(["688001.SH", "300001.SZ"], PortfolioPolicy())
    assert rules["688001.SH"].lot_size == 200
    assert rules["300001.SZ"].lot_size == 100


def _bars(code="688001.SH", price=10.0):
    return pd.DataFrame([{"ts_code": code, "trade_date": date, "open": price,
        "high": price, "low": price, "close": price, "pre_close": price,
        "vol": 100_000, "amount": 100_000} for date in ("20240902", "20240903", "20240904")])


def test_cost_prefilter_receives_board_metadata():
    bars = _bars()
    bars.loc[1, ["open", "high", "low", "close"]] = 12.0
    result = cost_adjusted_trade(bars, {"ok": True, "entry_index": 1, "exit_index": 2,
                                      "exit": "time", "exit_price": 10.0})
    assert result["filled"] is False
    assert "涨停" in result["reason"]
    bars.loc[1, ["open", "high", "low", "close"]] = 11.0
    assert cost_adjusted_trade(bars, {"ok": True, "entry_index": 1, "exit_index": 2,
                                      "exit": "time", "exit_price": 10.0})["filled"]


def test_portfolio_uses_star_step_and_maximum_order():
    bars = _bars(price=1.0)
    trades = [{"ts_code": "688001.SH", "date": "20240902", "entry_date": "20240903",
               "exit_date": "20240904", "exit": "time", "exit_price": 1.0, "cost": {"filled": True}}]
    result = simulate_portfolio(trades, bars, policy=PortfolioPolicy())
    entries = [event for event in result["events"] if event["event"] == "ENTRY_FILLED"]
    assert len(entries) == 1 and entries[0]["qty"] == 50_000
    assert result["portfolio_open_positions"] == 0
    # Same literal signal with unaffordable STAR minimum cannot create 100 shares.
    small = simulate_portfolio(trades, bars, policy=PortfolioPolicy(initial_cash_fen=150_000))
    assert small["portfolio_n_entries"] == 0


def test_legacy_main_board_default_is_unchanged():
    args = {"entry_open": 10, "entry_high": 10.5, "entry_low": 9.8, "entry_vol": 100_000,
        "entry_pre_close": 10, "exit_open": 11, "exit_high": 11.5, "exit_low": 10.8,
        "exit_vol": 100_000, "exit_pre_close": 10.5}
    assert simulate_round_trip(**args).to_dict() == simulate_round_trip(
        **args, ts_code="600001.SH", entry_date="20240902", exit_date="20240903").to_dict()


def test_blocked_sell_retains_buy_and_retries_instead_of_deleting_loss():
    bars = _bars()
    bars.loc[2, ["open", "high", "low", "close"]] = 8.0
    next_bar = {**bars.iloc[2].to_dict(), "trade_date": "20240905", "pre_close": 8.0,
                "open": 7.5, "high": 8.0, "low": 7.0, "close": 7.5}
    bars = pd.concat([bars, pd.DataFrame([next_bar])], ignore_index=True)
    cost = cost_adjusted_trade(bars, {"ok": True, "entry_index": 1, "exit_index": 2,
                                    "exit": "stop", "exit_price": 8.0})
    assert cost["entry_filled"] and not cost["filled"]
    trade = {"ts_code": "688001.SH", "date": "20240902", "entry_date": "20240903",
             "exit_date": "20240904", "exit": "stop", "exit_price": 8.0, "cost": cost}
    result = simulate_portfolio([trade], bars, policy=PortfolioPolicy())
    assert result["portfolio_n_entries"] == 1
    assert result["portfolio_rejection_reasons"]["EXIT_LIMIT_DOWN_ONE_SIDE"] == 1
    assert result["portfolio_final_equity_fen"] < PortfolioPolicy().initial_cash_fen
    exits = [row for row in result["events"] if row["event"] == "EXIT_FILLED"]
    assert exits[0]["trade_date"] == "20240905"


def population():
    return [{"ts_code": f"{prefix}{n:03d}.{exchange}", "market": board, "list_date": "20200101"}
            for prefix, exchange, board in [("300", "SZ", "创业板"), ("688", "SH", "科创板")]
            for n in range(70)]


def test_dual_selection_is_balanced_deterministic_and_ignores_returns():
    rows = population()
    rows += [{"ts_code": "600001.SH", "market": "主板", "list_date": "20000101"},
             {"ts_code": "301888.SZ", "market": "创业板", "list_date": "20240201"},
             {"ts_code": "689009.SH", "market": "科创板", "list_date": "20200101"}]
    result = select_dual_board(rows)
    assert result["counts"] == {"创业板": 50, "科创板": 50}
    assert len(result["codes"]) == 100 and len(result["population"]) == 140
    changed = [{**row, "future_return": 100_000, "name": "ST ignored"} for row in reversed(rows)]
    assert select_dual_board(changed) == result


def test_insufficient_or_duplicate_population_does_not_change_sample():
    with pytest.raises(ValueError, match="不足50"):
        select_dual_board(population()[:40])
    with pytest.raises(ValueError, match="重复"):
        select_dual_board(population() + population()[:1])


def test_registration_is_immutable(tmp_path):
    path = tmp_path / "registration.json"
    write_once(path, {"codes": ["300001.SZ"]})
    write_once(path, {"codes": ["300001.SZ"]})
    with pytest.raises(ValueError, match="禁止覆盖"):
        write_once(path, {"codes": ["600001.SH"]})
