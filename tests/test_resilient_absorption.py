from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from ab_screener.research.pit_reader import ResearchPitSnapshot
from ab_screener.research.resilient_absorption import (
    BASE_ENTRY_MECHANISM_ID,
    CONTEXT_BENCHMARK_RETURN,
    RESILIENT_ABSORPTION_ID,
    ResearchEntryMechanismError,
    attach_frozen_benchmark_context,
    entry_mechanism_identity,
    evaluate_entry_mechanism,
    prepare_signal_market_context,
    resolve_requested_entry_mechanism,
    signal_kwargs_for_entry_mechanism,
    split_signal_kwargs,
)
from optimizer import _detect_signals_for_code


def _bars() -> tuple[pd.DataFrame, dict[str, object]]:
    dates = pd.bdate_range("2026-01-05", periods=13).strftime("%Y%m%d").tolist()
    returns = [
        0.0,
        0.002,
        0.010,
        -0.002,
        0.008,
        -0.001,
        0.006,
        -0.002,
        0.007,
        -0.001,
        0.005,
        0.004,
        0.030,
    ]
    closes = [10.0]
    for value in returns[1:]:
        closes.append(closes[-1] * (1.0 + value))
    highs = [value * 1.01 for value in closes]
    lows = [value * 0.99 for value in closes]
    highs[-1] = closes[-1] * 1.01
    lows[-1] = closes[-1] * 0.96
    benchmark_returns = [
        None,
        0.001,
        -0.010,
        -0.010,
        -0.005,
        -0.008,
        0.002,
        0.001,
        0.003,
        0.001,
        0.002,
        0.001,
        0.004,
    ]
    frame = pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "trade_date": dates,
            "close": closes,
            "high": highs,
            "low": lows,
            "vol": [100.0] * len(dates),
            CONTEXT_BENCHMARK_RETURN: benchmark_returns,
        }
    )
    return frame, {"breakout_date": dates[-1], "box_days": 10}


def _snapshot(daily: pd.DataFrame, benchmark: pd.DataFrame) -> ResearchPitSnapshot:
    return ResearchPitSnapshot(
        decision_at="2026-01-30T18:00:00+08:00",
        data_start=str(daily["trade_date"].min()),
        data_end=str(daily["trade_date"].max()),
        universe=("000001.SZ",),
        universe_sha256="u" * 64,
        dataset_fingerprint="d" * 64,
        daily=daily,
        benchmark_code="000300.SH",
        benchmark_sha256="b" * 64,
        benchmark_daily=benchmark,
    )


def test_mechanism_identity_is_versioned_and_request_mismatch_fails_closed() -> None:
    identity = entry_mechanism_identity(RESILIENT_ABSORPTION_ID)

    assert identity["semantic_hash"] == entry_mechanism_identity(
        RESILIENT_ABSORPTION_ID
    )["semantic_hash"]
    assert identity["parameter_search"] == "none"
    assert resolve_requested_entry_mechanism(identity) == identity
    assert resolve_requested_entry_mechanism(None)["id"] == BASE_ENTRY_MECHANISM_ID
    with pytest.raises(ResearchEntryMechanismError, match="语义指纹"):
        resolve_requested_entry_mechanism({**identity, "semantic_hash": "tampered"})
    with pytest.raises(ResearchEntryMechanismError, match="未知"):
        split_signal_kwargs({"entry_mechanism_id": "UNKNOWN"})


def test_signal_kwargs_separate_mechanism_metadata_from_detector_parameters() -> None:
    detector, mechanism_id = split_signal_kwargs(
        {
            **signal_kwargs_for_entry_mechanism(RESILIENT_ABSORPTION_ID),
            "box_max_amp": 0.2,
        }
    )

    assert mechanism_id == RESILIENT_ABSORPTION_ID
    assert detector == {"box_max_amp": 0.2}


def test_resilient_absorption_passes_fixed_economic_neutral_boundaries() -> None:
    bars, signal = _bars()

    result = evaluate_entry_mechanism(RESILIENT_ABSORPTION_ID, bars, signal)

    assert result["passed"] is True
    assert result["failed_checks"] == []
    assert {row["id"] for row in result["checks"]} == {
        "relative_resilience_on_benchmark_down_days",
        "positive_volume_weighted_box_pressure",
        "breakout_upper_half_close",
    }
    assert all(float(row["actual"]) > 0 for row in result["checks"])


def test_mechanism_ignores_every_bar_after_breakout() -> None:
    bars, signal = _bars()
    original = evaluate_entry_mechanism(RESILIENT_ABSORPTION_ID, bars, signal)
    future = bars.iloc[-1].copy()
    future["trade_date"] = "20991231"
    future["close"] = 0.01
    future["high"] = 1000.0
    future["low"] = 0.001
    future["vol"] = 10**12
    future[CONTEXT_BENCHMARK_RETURN] = -0.99

    extended = pd.concat([bars, pd.DataFrame([future])], ignore_index=True)
    replay = evaluate_entry_mechanism(RESILIENT_ABSORPTION_ID, extended, signal)

    assert replay == original


def test_missing_context_and_zero_range_breakout_fail_closed() -> None:
    bars, signal = _bars()
    missing = bars.drop(columns=[CONTEXT_BENCHMARK_RETURN])
    missing_result = evaluate_entry_mechanism(RESILIENT_ABSORPTION_ID, missing, signal)
    zero_range = bars.copy()
    zero_range.loc[zero_range.index[-1], "high"] = zero_range.iloc[-1]["close"]
    zero_range.loc[zero_range.index[-1], "low"] = zero_range.iloc[-1]["close"]
    range_result = evaluate_entry_mechanism(RESILIENT_ABSORPTION_ID, zero_range, signal)

    assert missing_result["passed"] is False
    assert missing_result["reason"] == "MECHANISM_CONTEXT_INCOMPLETE"
    assert range_result["passed"] is False
    failed = {row["id"] for row in range_result["checks"] if not row["passed"]}
    assert failed == {"breakout_upper_half_close"}


def test_negative_relative_resilience_is_rejected_without_changing_other_rules() -> None:
    bars, signal = _bars()
    box_indexes = list(range(2, 6))
    bars.loc[box_indexes, CONTEXT_BENCHMARK_RETURN] = -0.0001
    bars.loc[box_indexes, "close"] = [9.8, 9.7, 9.6, 9.5]

    result = evaluate_entry_mechanism(RESILIENT_ABSORPTION_ID, bars, signal)

    assert result["passed"] is False
    assert "relative_resilience_on_benchmark_down_days" in result["failed_checks"]


def test_frozen_benchmark_context_is_many_to_one_and_duplicate_dates_are_rejected() -> None:
    bars, _signal = _bars()
    daily = bars.drop(columns=[CONTEXT_BENCHMARK_RETURN])
    benchmark = pd.DataFrame(
        {
            "trade_date": daily["trade_date"],
            "close": [100.0 + index for index in range(len(daily))],
        }
    )

    merged = attach_frozen_benchmark_context(daily, benchmark)

    assert merged[CONTEXT_BENCHMARK_RETURN].notna().sum() == len(benchmark) - 1
    with pytest.raises(ResearchEntryMechanismError, match="重复"):
        attach_frozen_benchmark_context(
            daily,
            pd.concat([benchmark, benchmark.iloc[[0]]], ignore_index=True),
        )


def test_non_base_mechanism_requires_bound_pit_benchmark() -> None:
    bars, _signal = _bars()
    daily = bars.drop(columns=[CONTEXT_BENCHMARK_RETURN])
    kwargs = signal_kwargs_for_entry_mechanism(RESILIENT_ABSORPTION_ID)

    with pytest.raises(ResearchEntryMechanismError, match="PIT"):
        prepare_signal_market_context(
            daily,
            research_snapshot=None,
            start=str(daily["trade_date"].min()),
            end=str(daily["trade_date"].max()),
            signal_kwargs=kwargs,
        )

    benchmark = pd.DataFrame(
        {
            "ts_code": "000300.SH",
            "trade_date": daily["trade_date"],
            "close": [100.0 + index for index in range(len(daily))],
        }
    )
    snapshot = _snapshot(daily, benchmark)
    wrong_benchmark = replace(snapshot, benchmark_code="000905.SH")
    with pytest.raises(ResearchEntryMechanismError, match="要求 PIT 基准"):
        prepare_signal_market_context(
            daily,
            research_snapshot=wrong_benchmark,
            start=str(daily["trade_date"].min()),
            end=str(daily["trade_date"].max()),
            signal_kwargs=kwargs,
        )


def test_optimizer_applies_mechanism_after_causal_base_signal(monkeypatch) -> None:
    dates = pd.bdate_range("2025-01-02", periods=80).strftime("%Y%m%d").tolist()
    frame = pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "trade_date": dates,
            "date": dates,
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.1,
            "vol": 100.0,
            CONTEXT_BENCHMARK_RETURN: -0.01,
        }
    )

    def fake_detect(window, **_kwargs):
        return {
            "is_breakout": True,
            "breakout_date": str(window.iloc[-1]["trade_date"]),
            "box_days": 20,
            "box_high": 10.0,
            "box_low": 9.0,
        }

    captured: list[str] = []

    def fake_mechanism(mechanism_id, _bars, _signal):
        captured.append(mechanism_id)
        return {"passed": True, "mechanism": entry_mechanism_identity(mechanism_id)}

    monkeypatch.setattr("signals.detect_accumulation_breakout", fake_detect)
    monkeypatch.setattr(
        "bench_volume.find_build_seqs",
        lambda *_args, **_kwargs: [{"bench_vol": 100.0}],
    )
    monkeypatch.setattr(
        "ab_screener.research.resilient_absorption.evaluate_entry_mechanism",
        fake_mechanism,
    )

    signals = _detect_signals_for_code(
        frame,
        [dates[-2]],
        {date: index for index, date in enumerate(dates)},
        dates,
        160,
        "A",
        [1.5],
        signal_kwargs=signal_kwargs_for_entry_mechanism(RESILIENT_ABSORPTION_ID),
    )

    assert len(signals) == 1
    assert captured == [RESILIENT_ABSORPTION_ID]
    assert signals[0]["entry_mechanism"]["passed"] is True
