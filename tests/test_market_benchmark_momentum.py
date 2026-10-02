from __future__ import annotations

import pandas as pd
import pytest

from ab_screener.research.intermediate_momentum import (
    COUNT,
    RANK,
    SCORE,
    attach_momentum_context,
    evaluate_momentum,
)
from ab_screener.research.market_benchmark import build_market_comparison, compare_account, price_window
from ab_screener.research.pit_reader import ResearchPitSnapshot
from ab_screener.research.professional_runner import _verdict


def snapshot() -> ResearchPitSnapshot:
    dates = ["20260102", "20260105", "20260106", "20260107"]
    benchmark = pd.DataFrame({"trade_date": dates, "close": [100.0, 110.0, 99.0, 120.0]})
    return ResearchPitSnapshot("2026-01-08T08:00:00+08:00", dates[0], dates[-1],
                               ("000001.SZ",), "universe", "data", benchmark.copy(),
                               benchmark_code="000300.SH", benchmark_sha256="frozen", benchmark_daily=benchmark)


def account() -> dict:
    return {"initial_equity_fen": "10000", "final_equity_fen": "11500", "events": [],
            "equity_curve": [{"trade_date": "20260106", "equity_fen": "9000"},
                             {"trade_date": "20260107", "equity_fen": "11500"}]}


def test_reference_anchors_before_start_and_fills_only_leading_cash() -> None:
    result = compare_account(snapshot(), "20260105", "20260107", account(), 0.15, 0.12)
    assert result["benchmark_return"] == pytest.approx(0.20)
    assert result["excess_return"] == pytest.approx(-0.05)
    assert result["stress_excess_return"] == pytest.approx(-0.08)
    assert result["benchmark_max_drawdown"] == pytest.approx(0.10)
    assert result["strategy_max_drawdown"] == pytest.approx(0.10)
    assert result["curve"][0]["strategy_nav"] == 1.0


def test_reference_missing_price_or_equity_or_wrong_total_fails_closed() -> None:
    source = snapshot()
    source.benchmark_daily.drop(index=2, inplace=True)
    with pytest.raises(ValueError, match="研究交易日"):
        price_window(source, "20260105", "20260107")
    broken = account()
    broken["equity_curve"][0]["trade_date"] = "20260105"
    with pytest.raises(ValueError, match="缺少 20260106"):
        compare_account(snapshot(), "20260105", "20260107", broken, 0.15)
    with pytest.raises(ValueError, match="末值不一致"):
        compare_account(snapshot(), "20260105", "20260107", account(), 0.99)
    with pytest.raises(ValueError):
        price_window(snapshot(), "20260102", "20260107")


def test_missing_account_is_not_a_zero_return_pass() -> None:
    windows = {"is": ["20260105", "20260107"], "oos": ["20260105", "20260107"], "wf": []}
    result = build_market_comparison(snapshot(), windows, {}, {"is": {}, "oos": {}}, None, None)
    assert result["status"] == "INSUFFICIENT"
    assert len(result["sha256"]) == 64
    verdict, _, reasons = _verdict(
        {"oos": {"net_n_trades": 100, "net_total_return": 0.15}},
        {"evidence_complete": True, "wf_pass": True},
        {"random": {"net_total_return": 0.0}}, {"metrics": {"net_total_return": 0.12}},
        market_comparison={"status": "COMPLETE", "oos": {"excess_return": -0.05, "stress_excess_return": -0.08}})
    assert verdict == "EXPLORATORY_WEAK"
    assert any("沪深300" in reason for reason in reasons)


def momentum_market() -> tuple[pd.DataFrame, list[str]]:
    dates = pd.bdate_range("2025-01-01", periods=150).strftime("%Y%m%d").tolist()
    return pd.DataFrame([{"ts_code": f"{code:06d}.SZ", "trade_date": day, "pct_chg": code / 100}
                         for code in range(60) for day in dates]), dates


def test_momentum_cross_section_skips_last_month_and_ignores_future() -> None:
    daily, dates = momentum_market()
    original = attach_momentum_context(daily, dates)
    day = dates[130]
    changed = daily.copy()
    changed.loc[changed.trade_date > dates[109], "pct_chg"] = 10.0
    replay = attach_momentum_context(changed, dates)
    columns = ["ts_code", SCORE, RANK, COUNT]
    pd.testing.assert_frame_equal(original[original.trade_date == day][columns].reset_index(drop=True),
                                  replay[replay.trade_date == day][columns].reset_index(drop=True))
    high = original[original.ts_code == "000059.SZ"]
    assert evaluate_momentum(high, {"breakout_date": day})["passed"] is True
    assert evaluate_momentum(original[original.ts_code == "000000.SZ"], {"breakout_date": day})["passed"] is False
    assert high.loc[high.trade_date == day, SCORE].iloc[0] == pytest.approx((1.0059 ** 105) - 1)


def test_momentum_missing_history_ties_small_universe_and_duplicates() -> None:
    daily, dates = momentum_market()
    missing = daily[~((daily.ts_code == "000059.SZ") & (daily.trade_date == dates[90]))]
    result = attach_momentum_context(missing, dates)
    assert evaluate_momentum(result[result.ts_code == "000059.SZ"], {"breakout_date": dates[130]})["passed"] is False
    small = attach_momentum_context(daily[daily.ts_code < "000030.SZ"], dates)
    assert evaluate_momentum(small[small.ts_code == "000029.SZ"], {"breakout_date": dates[130]})["passed"] is False
    daily["pct_chg"] = 1.0
    tied = attach_momentum_context(daily, dates)
    assert not (tied[RANK] <= .30).any()
    with pytest.raises(ValueError, match="重复"):
        attach_momentum_context(pd.concat([daily, daily.iloc[:1]]), dates)


def test_pair_target_requires_all_prespecified_checks_and_identical_protocol() -> None:
    from copy import deepcopy

    from scripts.run_market_excess_pair import evaluate_pair

    request = {key: "same" for key in ("windows", "universe", "parameters", "knowledge_cutoff", "code_version", "sample_step")}
    base = {"snapshot": {"hash": "same"}, "request": request,
            "market_comparison": {"status": "COMPLETE", "oos": {"strategy_return": 0.02}}}
    factor = {"snapshot": base["snapshot"], "request": request,
              "market_comparison": {"status": "COMPLETE", "oos": {"strategy_return": .30, "excess_return": .18,
                 "sessions": 220, "strategy_max_drawdown": .10, "stress_excess_return": .08},
                 "wf": [{"excess_return": .02}, {"excess_return": -.01}, {"excess_return": .03}]},
              "cost_stress": {"metrics": {"net_total_return": .20}}, "selected": {"oos": {"net_n_trades": 45}},
              "wf": {"evidence_complete": True}}
    accepted = evaluate_pair(base, factor)
    assert accepted["status"] == "HISTORICAL_TARGET_MET"
    assert not accepted["candidate_eligible"] and not accepted["can_claim_edge"]
    broken = deepcopy(factor)
    broken["request"]["universe"] = "cherry-picked"
    assert evaluate_pair(base, broken)["status"] == "TARGET_NOT_MET"
    broken = deepcopy(factor)
    broken["wf"]["evidence_complete"] = False
    assert evaluate_pair(base, broken)["status"] == "TARGET_NOT_MET"
