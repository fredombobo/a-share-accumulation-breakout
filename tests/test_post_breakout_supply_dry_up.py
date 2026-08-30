from __future__ import annotations

import pandas as pd

from ab_screener.research.post_breakout_supply_dry_up import (
    POST_BREAKOUT_SUPPLY_DRY_UP_ID,
    add_exchange_session_check,
    detect_post_breakout_supply_dry_up,
    evaluate_post_breakout_supply_dry_up,
)
from ab_screener.research.resilient_absorption import entry_mechanism_identity
from optimizer import _detect_signals_for_code


def _confirmation_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "trade_date": ["20260803", "20260804", "20260805"],
            "date": ["2026-08-03", "2026-08-04", "2026-08-05"],
            "open": [9.8, 10.1, 10.2],
            "high": [10.0, 10.8, 10.5],
            "low": [9.7, 9.9, 9.8],
            "close": [9.9, 10.5, 10.4],
            "vol": [100.0, 200.0, 100.0],
        }
    )


def _base_signal(window: pd.DataFrame, **_kwargs) -> dict[str, object]:
    breakout_date = str(window.iloc[-1]["trade_date"])
    return {
        "is_breakout": True,
        "breakout_date": breakout_date,
        "box_days": 60,
        "box_high": 10.0,
        "box_low": 9.0,
        "reasons": ["冻结严格突破"],
    }


def test_confirmation_accepts_fixed_economic_mechanism_and_delays_signal(monkeypatch) -> None:
    monkeypatch.setattr(
        "ab_screener.signals.detect_accumulation_breakout",
        _base_signal,
    )

    signal = detect_post_breakout_supply_dry_up(_confirmation_bars())

    assert signal["is_breakout"] is True
    assert signal["initial_breakout_date"] == "20260804"
    assert signal["confirmation_date"] == signal["breakout_date"] == "20260805"
    evidence = signal["entry_mechanism_evidence"]
    assert evidence["passed"] is True
    assert {item["id"] for item in evidence["checks"]} == {
        "next_stock_bar_confirmation",
        "close_accepted_above_frozen_box_high",
        "post_breakout_volume_dry_up",
        "confirmation_upper_half_close",
    }
    assert len(evidence["evidence_sha256"]) == 64


def test_confirmation_evidence_ignores_every_bar_after_t1(monkeypatch) -> None:
    monkeypatch.setattr(
        "ab_screener.signals.detect_accumulation_breakout",
        _base_signal,
    )
    bars = _confirmation_bars()
    signal = detect_post_breakout_supply_dry_up(bars)
    original = evaluate_post_breakout_supply_dry_up(bars, signal)
    future = bars.iloc[-1].copy()
    future["trade_date"] = "20991231"
    future["date"] = "2099-12-31"
    future["high"] = 1_000.0
    future["low"] = 0.001
    future["close"] = 0.01
    future["vol"] = 10**12

    replay = evaluate_post_breakout_supply_dry_up(
        pd.concat([bars, pd.DataFrame([future])], ignore_index=True),
        signal,
    )

    assert replay == original


def test_confirmation_failures_are_explicit_and_do_not_change_thresholds(monkeypatch) -> None:
    monkeypatch.setattr(
        "ab_screener.signals.detect_accumulation_breakout",
        _base_signal,
    )
    cases = {
        "close_accepted_above_frozen_box_high": {"close": 10.0},
        "post_breakout_volume_dry_up": {"vol": 200.0},
        "confirmation_upper_half_close": {"close": 9.9, "high": 10.5, "low": 9.8},
    }

    for expected_failure, changes in cases.items():
        bars = _confirmation_bars()
        for column, value in changes.items():
            bars.loc[bars.index[-1], column] = value
        signal = detect_post_breakout_supply_dry_up(bars)
        evidence = signal["entry_mechanism_evidence"]
        assert signal["is_breakout"] is False
        assert expected_failure in evidence["failed_checks"]

    missing = detect_post_breakout_supply_dry_up(
        _confirmation_bars().drop(columns=["vol"])
    )
    assert missing["reasons"] == ["MECHANISM_CONTEXT_INCOMPLETE"]


def test_exchange_session_adjacency_is_hashed_and_fails_closed() -> None:
    base = {
        "passed": True,
        "checks": [],
        "mechanism": entry_mechanism_identity(POST_BREAKOUT_SUPPLY_DRY_UP_ID),
    }
    adjacent = add_exchange_session_check(
        base,
        initial_breakout_date="20260804",
        confirmation_date="20260805",
        exchange_session_gap=1,
    )
    skipped = add_exchange_session_check(
        base,
        initial_breakout_date="20260803",
        confirmation_date="20260805",
        exchange_session_gap=2,
    )

    assert adjacent["passed"] is True
    assert skipped["passed"] is False
    assert skipped["failed_checks"] == ["next_exchange_session_confirmation"]
    assert adjacent["evidence_sha256"] != skipped["evidence_sha256"]


def test_optimizer_rejects_confirmation_after_a_missing_exchange_session(monkeypatch) -> None:
    dates = pd.bdate_range("2026-04-01", periods=80).strftime("%Y%m%d").tolist()
    date_index = {value: index for index, value in enumerate(dates)}
    frame = pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "trade_date": dates,
            "date": pd.to_datetime(dates).strftime("%Y-%m-%d"),
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.4,
            "vol": 100.0,
        }
    )
    gap = 1

    def fake_detect(mechanism_id, window, _kwargs):
        confirmation = str(window.iloc[-1]["trade_date"])
        initial = dates[date_index[confirmation] - gap]
        return {
            "is_breakout": True,
            "breakout_date": confirmation,
            "confirmation_date": confirmation,
            "initial_breakout_date": initial,
            "box_high": 10.0,
            "box_low": 9.0,
        }

    monkeypatch.setattr(
        "ab_screener.research.resilient_absorption.detect_entry_signal",
        fake_detect,
    )
    monkeypatch.setattr(
        "ab_screener.research.resilient_absorption.evaluate_entry_mechanism",
        lambda mechanism_id, _bars, _signal: {
            "passed": True,
            "checks": [],
            "mechanism": entry_mechanism_identity(mechanism_id),
        },
    )
    monkeypatch.setattr(
        "bench_volume.find_build_seqs",
        lambda *_args, **_kwargs: [{"bench_vol": 100.0}],
    )
    kwargs = {"entry_mechanism_id": POST_BREAKOUT_SUPPLY_DRY_UP_ID}
    sample_day = dates[-2]

    accepted = _detect_signals_for_code(
        frame,
        [sample_day],
        date_index,
        dates,
        160,
        "A",
        [1.5],
        signal_kwargs=kwargs,
    )
    gap = 2
    rejected = _detect_signals_for_code(
        frame,
        [sample_day],
        date_index,
        dates,
        160,
        "A",
        [1.5],
        signal_kwargs=kwargs,
    )

    assert len(accepted) == 1
    assert accepted[0]["day"] == sample_day
    assert accepted[0]["entry_i"] == len(dates) - 2
    assert accepted[0]["entry_mechanism"]["passed"] is True
    assert rejected == []
