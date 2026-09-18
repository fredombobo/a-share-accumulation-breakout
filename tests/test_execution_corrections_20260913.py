"""Synthetic F03/F04/gap regressions: no database, providers or research runs."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from ab_screener.domain.execution.fees import FeeParams
from ab_screener.domain.execution.fill_model import FillRequest, compute_fill
from ab_screener.domain.execution.models import EXECUTION_MODEL_VERSION, FEE_VERSION, MoneyError, Quote
from ab_screener.research.cost_adjustment import cost_adjusted_trade
from ab_screener.research.portfolio_accounting import (
    PORTFOLIO_MODEL_VERSION,
    PortfolioAccountingError,
    PortfolioPolicy,
    prepare_portfolio_market,
    simulate_portfolio,
)
from paper_trading.rules import default_rule
from trade_sim import simulate_trade


def bar(code, day, price=10, previous=10, **changes):
    return dict(ts_code=code, trade_date=day, open=float(price), high=float(price), low=float(price),
                close=float(price), pre_close=float(previous), vol=100_000, amount=100_000, **changes)


def trade(code, entry="20260902", exit_day="20260903", price=10, kind="time", **changes):
    return {"ts_code": code, "date": "20260901", "entry_date": entry, "exit_date": exit_day,
            "exit_price": price, "exit": kind, "cost": {"entry_filled": True, "filled": True}, **changes}


def full_policy(**changes):
    return PortfolioPolicy(**{"initial_cash_fen": 1_000_000, "single_name_weight_bps": 10_000,
        "gross_exposure_bps": 10_000, "minimum_cash_bps": 0, "daily_new_buy_bps": 10_000,
        "participation_bps": 10_000, **changes})


def zero_rules(codes):
    return {code: replace(default_rule(code), commission_bps=0, min_commission_fen=0,
                         sell_tax_bps=0, other_fee_bps=0, slippage_bps=0) for code in codes}


def quote(**changes):
    return Quote(**{"ts_code": "000001.SZ", "trade_date": "20260903",
                    "open_micro": 10_000_000, "high_micro": 11_000_000, "low_micro": 9_000_000,
                    "close_micro": 10_000_000, "vol": 100_000, "amount_fen": 100_000_000,
                    "pre_close_micro": 10_000_000, **changes})


@pytest.mark.parametrize("code,locked", [("000001.SZ", 9_000_000), ("300001.SZ", 8_000_000), ("688001.SH", 8_000_000)])
def test_real_limit_down_guard_cannot_be_overridden_by_planned_stop(code, locked):
    actual = quote(ts_code=code, open_micro=locked, high_micro=locked, low_micro=locked, close_micro=locked)
    result = compute_fill(actual, FillRequest(code, "SELL", "20260903", "stop",
        requested_qty=1000, position_qty=1000, reference_price_micro=9_300_000))
    assert result.filled is False
    assert result.reason == "LIMIT_DOWN_ONE_SIDE"
    assert result.fees.total_fen() == result.cash_delta_fen == 0
    assert actual.open_micro == locked


@pytest.mark.parametrize("reference", [True, 9.3, 0, -1])
def test_execution_reference_requires_positive_integer_micro(reference):
    with pytest.raises(MoneyError):
        FillRequest("000001.SZ", "SELL", "20260903", "bad", reference_price_micro=reference)


def test_default_reference_remains_open_and_override_changes_only_fill_price():
    actual = quote()
    fees = FeeParams(slippage_bps=0)
    request = FillRequest("000001.SZ", "SELL", "20260903", "reference", fees=fees,
                          requested_qty=1000, position_qty=1000)
    default = compute_fill(actual, request)
    close = compute_fill(actual, replace(request, reference_price_micro=10_500_000))
    assert default.price_micro == actual.open_micro
    assert close.price_micro == 10_500_000
    assert close.cash_delta_fen == close.notional_fen - close.fees.commission_fen - close.fees.stamp_tax_fen - close.fees.other_fee_fen
    assert close.fees.slippage_fen == 0


def test_f03_both_cost_and_portfolio_keep_bought_position_until_next_open():
    code = "000001.SZ"
    bars = pd.DataFrame([bar(code, "20260901"), bar(code, "20260902"),
                         bar(code, "20260903", 9, 10), bar(code, "20260904", 9, 9)])
    simulation = {"ok": True, "entry_index": 1, "exit_index": 2, "exit": "stop", "exit_price": 9.3}
    cost = cost_adjusted_trade(bars, simulation)
    assert cost["filled"] is False and cost["entry_filled"] is True
    assert cost["reason"] == "一字跌停无法卖出"
    result = simulate_portfolio([trade(code, price=9.3, kind="stop", cost=cost)], bars,
                                policy=full_policy(), rules=zero_rules([code]))
    retries = [event for event in result["events"] if event["event"] == "EXIT_RETRY"]
    exits = [event for event in result["events"] if event["event"] == "EXIT_FILLED"]
    assert len(retries) == len(exits) == 1
    assert retries[0]["trade_date"] == "20260903"
    assert retries[0]["execution_phase"] == "INTRADAY"
    assert exits[0]["trade_date"] == "20260904" and exits[0]["execution_phase"] == "OPEN"
    assert exits[0]["price_micro"] == 9_000_000
    assert result["portfolio_open_positions"] == 0


def test_gap_stop_cannot_fill_above_actual_open_in_either_cost_layer():
    code = "000001.SZ"
    rows = [bar(code, day) for day in ("20260901", "20260902", "20260903")]
    rows[-1].update(open=9.0, high=9.2, low=8.8, close=9.0)
    bars = pd.DataFrame(rows)
    cost = cost_adjusted_trade(bars, {"ok": True, "entry_index": 1, "exit_index": 2, "exit": "stop", "exit_price": 9.3})
    assert cost["filled"] is True and cost["price"] <= 9.0
    result = simulate_portfolio([trade(code, price=9.3, kind="stop")], bars,
                                policy=full_policy(), rules=zero_rules([code]))
    exit_event = next(event for event in result["events"] if event["event"] == "EXIT_FILLED")
    assert exit_event["price_micro"] == exit_event["reference_price_micro"] == 9_000_000
    assert exit_event["actual_open_micro"] == 9_000_000
    assert exit_event["planned_exit_price_micro"] == 9_300_000


def test_f04_default_eighty_percent_cap_is_checked_before_close_sale():
    codes = [f"{index:06d}.SZ" for index in range(1, 10)]
    dates = ["20260831", "20260901", "20260902", "20260903", "20260904", "20260907", "20260908"]
    bars = pd.DataFrame([bar(code, day) for code in codes for day in dates])
    candidates = [trade(code, dates[index // 2 + 1], dates[5] if index == 0 else dates[6],
                        date=dates[index // 2]) for index, code in enumerate(codes)]
    result = simulate_portfolio(candidates, bars, policy=PortfolioPolicy(), rules=zero_rules(codes))
    events = [event for event in result["events"] if event["trade_date"] == "20260907"]
    assert events[0]["event"] == "ENTRY_REJECTED" and events[0]["ts_code"] == codes[-1]
    assert events[1]["event"] == "EXIT_FILLED" and events[1]["execution_phase"] == "CLOSE"
    assert result["portfolio_max_gross_exposure_bps"] == 8000


@pytest.mark.parametrize("phase", ["INTRADAY", "CLOSE"])
@pytest.mark.parametrize("multiplier", [10_000, 20_000])
def test_late_sale_never_funds_open_buy_even_with_actual_fees(phase, multiplier):
    codes = ["000001.SZ", "000002.SZ"]
    bars = pd.DataFrame([bar(code, day) for code in codes
                         for day in ("20260901", "20260902", "20260903", "20260904")])
    candidates = [trade(codes[0], exit_phase=phase), trade(codes[1], "20260903", "20260904")]
    result = simulate_portfolio(candidates, bars, policy=full_policy(cost_multiplier_bps=multiplier))
    assert result["portfolio_n_entries"] == 1
    assert any(event["event"] == "ENTRY_REJECTED" and event["ts_code"] == codes[1] for event in result["events"])
    filled = [event for event in result["events"] if event["filled"]]
    assert result["portfolio_final_equity_fen"] == 1_000_000 + sum(event["cash_delta_fen"] for event in filled)


def test_open_exit_proceeds_can_fund_open_buy_and_preserve_t1():
    codes = ["000001.SZ", "000002.SZ"]
    bars = pd.DataFrame([bar(code, day) for code in codes
                         for day in ("20260901", "20260902", "20260903", "20260904")])
    candidates = [trade(codes[0], exit_phase="OPEN"), trade(codes[1], "20260903", "20260904")]
    result = simulate_portfolio(candidates, bars, policy=full_policy(), rules=zero_rules(codes))
    events = [event["event"] for event in result["events"] if event["trade_date"] == "20260903"]
    assert events == ["EXIT_FILLED", "ENTRY_FILLED"]
    assert result["portfolio_n_entries"] == 2
    with pytest.raises(PortfolioAccountingError, match="时间顺序非法"):
        simulate_portfolio([trade(codes[0], exit_day="20260902")], bars, policy=full_policy())
    same_day = cost_adjusted_trade(bars[bars.ts_code == codes[0]].reset_index(drop=True),
        {"ok": True, "entry_index": 1, "exit_index": 1, "exit": "time", "exit_price": 10})
    assert same_day["filled"] is False and same_day["entry_filled"] is True
    assert same_day["reason"] == "T1_NOT_SELLABLE"


def test_partial_exit_attempts_once_daily_and_allocates_cost_exactly():
    code = "000001.SZ"
    dates = ["20260901", "20260902", "20260903", "20260904", "20260907", "20260908"]
    rows = [bar(code, day) for day in dates]
    for row in rows[2:]:
        row["vol"] = 3  # 300 shares, policy participation 100%
    result = simulate_portfolio([trade(code)], pd.DataFrame(rows),
                                policy=full_policy(), rules=zero_rules([code]))
    exits = [event for event in result["events"] if event["event"] == "EXIT_FILLED"]
    assert [event["qty"] for event in exits] == [300, 300, 300, 100]
    assert [event["execution_phase"] for event in exits] == ["CLOSE", "OPEN", "OPEN", "OPEN"]
    assert len({event["trade_date"] for event in exits}) == 4
    assert sum(event["allocated_cost_fen"] for event in exits) == 1_000_000
    assert result["portfolio_final_equity_fen"] == result["portfolio_initial_equity_fen"]


def test_zero_open_and_zero_close_carry_last_known_value_without_freeing_risk_budget():
    codes = ["000001.SZ", "000002.SZ"]
    dates = ["20260901", "20260902", "20260903", "20260904"]
    rows = [bar(code, day) for code in codes for day in dates]
    next(row for row in rows if row["ts_code"] == codes[0] and row["trade_date"] == dates[2]).update(
        open=0.0, high=0.0, low=0.0, close=0.0, vol=0)
    policy = full_policy(single_name_weight_bps=8000, gross_exposure_bps=8000)
    candidates = [trade(codes[0]), trade(codes[1], "20260903", "20260904")]
    result = simulate_portfolio(candidates, pd.DataFrame(rows), policy=policy, rules=zero_rules(codes))
    day = next(row for row in result["equity_curve"] if row["trade_date"] == "20260903")
    assert day["equity_fen"] == 1_000_000 and day["market_value_fen"] == 800_000
    assert result["portfolio_n_entries"] == 1
    assert any(event["event"] == "MARK_CARRY_FORWARD" and event["reason"] == "INVALID_CLOSE" for event in result["events"])
    assert len([event for event in result["events"] if event["event"] == "EXIT_RETRY"]) == 1


def test_new_identity_rejects_old_policy_and_prepared_market():
    assert EXECUTION_MODEL_VERSION == "v2.1.3"
    assert PORTFOLIO_MODEL_VERSION == "research-portfolio-v2.2.0"
    assert FEE_VERSION == "v2-fixed-2026-08-18"
    with pytest.raises(PortfolioAccountingError, match="未知组合模型"):
        PortfolioPolicy(version="research-portfolio-v2.1.0")
    bars = pd.DataFrame([bar("000001.SZ", day) for day in ("20260901", "20260902", "20260903")])
    prepared = prepare_portfolio_market(bars, full_policy())
    with pytest.raises(PortfolioAccountingError, match="组合配置版本不一致"):
        simulate_portfolio([trade("000001.SZ")], replace(prepared, policy_fingerprint="old-model"), policy=full_policy())


@pytest.mark.parametrize("kind,phase", [("stop", "INTRADAY"), ("target", "INTRADAY"), ("time", "CLOSE")])
def test_frozen_signal_params_now_carry_execution_phase(kind, phase):
    rows = [bar("000001.SZ", day) for day in ("20260901", "20260902", "20260903")]
    rows[-1].update(open=10.0, high=11.5 if kind == "target" else 10.5,
                    low=9.0 if kind == "stop" else 9.5, close=10.2)
    params = {"stop_pct": 0.07, "target_pct": 0.10, "max_hold": 3}
    before = dict(params)
    simulation = simulate_trade(pd.DataFrame(rows), 0, "fixed", params)
    assert simulation["exit"] == kind and simulation["exit_phase"] == phase
    assert params == before
    if kind == "time":
        assert simulation["exit_price"] == 10.2


def test_prepared_request_with_old_model_is_rejected_before_any_data_access():
    from ab_screener.research.professional_grid import ProfessionalGridError
    from ab_screener.research.professional_runner import execute_professional_run

    with pytest.raises(ProfessionalGridError, match="模型已变化"):
        execute_professional_run("must-not-be-opened.db", {"portfolio_model": {"version": "old"}},
                                 progress=lambda *args: None, cancel_check=lambda: False)


@pytest.mark.parametrize("baseline", ["random", "ma"])
def test_baseline_time_exits_keep_open_semantics_when_close_differs(monkeypatch, baseline):
    from ab_screener.research import baselines

    rows = [bar("000001.SZ", f"202609{index + 1:02d}") for index in range(8)]
    for row, close in zip(rows, [10.0, 10.0, 9.0, 11.0, 13.0, 14.0, 15.0, 16.0]):
        row.update(close=close, high=max(10.1, close + 0.1), low=8.9)
    captured = []
    monkeypatch.setattr(baselines, "_apply_portfolio_metrics",
                        lambda out, candidates, daily, policy: captured.extend(candidates))
    if baseline == "random":
        baselines.random_baseline_trades(pd.DataFrame(rows), n_trades=1, hold_days=1)
    else:
        baselines.ma_cross_baseline(pd.DataFrame(rows), fast=2, slow=3, hold_days=1)
    assert captured
    for row in captured:
        assert row["exit"] == "time" and row["exit_phase"] == "OPEN"
        assert row["exit_price"] == 10.0
