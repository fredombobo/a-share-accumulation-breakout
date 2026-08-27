from __future__ import annotations

import pandas as pd
import pytest

from ab_screener.research.portfolio_accounting import (
    PORTFOLIO_MODEL_VERSION,
    PortfolioAccountingError,
    PortfolioPolicy,
    portfolio_gate_metrics,
    prepare_portfolio_market,
    simulate_portfolio,
)


def _market(*, zero_volume_code: str | None = None) -> pd.DataFrame:
    rows = []
    dates = ["20260803", "20260804", "20260805", "20260806"]
    for code, base in (("000001.SZ", 10), ("000002.SZ", 20), ("000003.SZ", 30)):
        for index, date in enumerate(dates):
            px = base + index
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": date,
                    "open": px,
                    "high": px + 1,
                    "low": px - 1,
                    "close": px + 0.5,
                    "pre_close": px - 0.5,
                    "vol": 0 if code == zero_volume_code else 100_000,
                    "amount": 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def _trade(code: str, exit_price: float = 12.0) -> dict:
    return {
        "ts_code": code,
        "date": "20260803",
        "entry_date": "2026-08-04",
        "exit_date": "2026-08-06",
        "exit": "time",
        "exit_price": exit_price,
        "cost": {"filled": True},
    }


def test_overlapping_signals_share_cash_and_respect_risk_caps() -> None:
    policy = PortfolioPolicy()
    result = simulate_portfolio(
        [_trade("000001.SZ", 13), _trade("000002.SZ", 23), _trade("000003.SZ", 33)],
        _market(),
        policy=policy,
    )

    entries = [event for event in result["events"] if event["event"] == "ENTRY_FILLED"]
    assert len(entries) == 3
    assert result["portfolio_model_version"] == PORTFOLIO_MODEL_VERSION
    assert result["portfolio_max_gross_exposure_bps"] <= policy.gross_exposure_bps
    assert result["portfolio_min_cash_fen"] >= 0
    # Three same-day candidates share the 20% daily budget; none receives 100% equity.
    assert all(event["notional_fen"] < policy.initial_cash_fen // 10 for event in entries)
    assert sum(-event["cash_delta_fen"] for event in entries) <= (
        policy.initial_cash_fen * policy.daily_new_buy_bps // 10_000
    )
    assert result["portfolio_open_positions"] == 0


def test_equity_is_cash_plus_market_value_and_deterministic() -> None:
    policy = PortfolioPolicy()
    first = simulate_portfolio([_trade("000001.SZ", 13)], _market(), policy=policy)
    second = simulate_portfolio([_trade("000001.SZ", 13)], _market(), policy=policy)

    assert first["portfolio_equity_sha256"] == second["portfolio_equity_sha256"]
    assert first["events"] == second["events"]
    assert len(first["portfolio_daily_returns"]) == len(first["equity_curve"])
    for row in first["equity_curve"]:
        assert row["equity_fen"] == row["cash_fen"] + row["market_value_fen"]
    metrics = portfolio_gate_metrics(first)
    assert metrics["net_max_drawdown"] == first["portfolio_max_drawdown"]
    assert metrics["portfolio_model_version"] == PORTFOLIO_MODEL_VERSION


def test_zero_volume_buy_is_rejected_without_changing_cash() -> None:
    policy = PortfolioPolicy()
    result = simulate_portfolio(
        [_trade("000001.SZ")],
        _market(zero_volume_code="000001.SZ"),
        policy=policy,
    )

    assert result["portfolio_n_entries"] == 0
    assert result["portfolio_final_equity_fen"] == policy.initial_cash_fen
    assert result["portfolio_rejection_reasons"]["NO_VOLUME"] == 1


def test_suspended_zero_quote_buy_is_rejected_without_exception() -> None:
    market = _market()
    mask = (market["ts_code"] == "000001.SZ") & (market["trade_date"] == "20260804")
    market.loc[mask, ["open", "high", "low", "vol"]] = 0
    result = simulate_portfolio(
        [_trade("000001.SZ")],
        market,
        policy=PortfolioPolicy(),
    )

    assert result["portfolio_n_entries"] == 0
    assert result["portfolio_rejection_reasons"]["NO_QUOTE"] == 1


def test_suspension_carries_last_close_and_delays_exit() -> None:
    market = _market()
    market = market[
        ~((market["ts_code"] == "000001.SZ") & (market["trade_date"].isin(["20260805", "20260806"])))
    ]
    result = simulate_portfolio([_trade("000001.SZ")], market, policy=PortfolioPolicy())
    assert result["portfolio_open_positions"] == 1
    assert result["portfolio_n_exits"] == 0
    assert result["portfolio_status"] == "INCOMPLETE_OPEN_POSITIONS"
    assert any(event["event"] == "MARK_CARRY_FORWARD" for event in result["events"])
    assert result["portfolio_rejection_reasons"]["EXIT_NO_QUOTE"] == 1


def test_missing_entry_quote_fails_closed() -> None:
    market = _market()
    market = market[~((market["ts_code"] == "000001.SZ") & (market["trade_date"] == "20260804"))]
    with pytest.raises(PortfolioAccountingError, match="缺少当日估值行情"):
        simulate_portfolio([_trade("000001.SZ")], market, policy=PortfolioPolicy())


def test_duplicate_candidate_fails_closed() -> None:
    trade = _trade("000001.SZ")
    with pytest.raises(PortfolioAccountingError, match="重复成交候选"):
        simulate_portfolio([trade, dict(trade)], _market(), policy=PortfolioPolicy())


def test_same_day_entry_and_exit_candidate_fails_closed() -> None:
    trade = {
        **_trade("000001.SZ"),
        "exit_date": "20260804",
    }
    with pytest.raises(PortfolioAccountingError, match="时间顺序非法"):
        simulate_portfolio([trade], _market(), policy=PortfolioPolicy())


def test_policy_rejects_impossible_risk_budget() -> None:
    with pytest.raises(PortfolioAccountingError, match="总仓位与最低现金"):
        PortfolioPolicy(gross_exposure_bps=9_500, minimum_cash_bps=1_000)


def test_prepared_market_cannot_be_reused_with_another_policy() -> None:
    policy = PortfolioPolicy()
    prepared = prepare_portfolio_market(_market(), policy)

    with pytest.raises(PortfolioAccountingError, match="组合配置版本不一致"):
        simulate_portfolio(
            [_trade("000001.SZ")],
            prepared,
            policy=PortfolioPolicy(initial_cash_fen=200_000_000),
        )


def test_overlapping_signal_for_active_instrument_is_explicitly_rejected() -> None:
    first = _trade("000001.SZ")
    overlapping = {
        **first,
        "date": "20260804",
        "entry_date": "20260805",
    }

    result = simulate_portfolio([first, overlapping], _market(), policy=PortfolioPolicy())

    assert result["portfolio_rejection_reasons"]["DUPLICATE_ACTIVE_POSITION"] == 1
    assert any(event.get("reason") == "DUPLICATE_ACTIVE_POSITION" for event in result["events"])
