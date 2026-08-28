from __future__ import annotations

import pandas as pd
import pytest

import optimizer
from ab_screener.research.baselines import random_baseline_trades
from ab_screener.research.pit_reader import ResearchPitError, ResearchPitSnapshot
from ab_screener.research.regime_filter import ResearchRegimeFilter, build_research_regime_filter
from ab_screener.research.trusted_run import execute_trusted_research


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    dates = pd.bdate_range("2026-05-01", periods=32).strftime("%Y%m%d").tolist()
    closes = [100.0] * 25 + [90.0] + [91.0] * 6
    benchmark = pd.DataFrame(
        {
            "ts_code": "000300.SH",
            "trade_date": dates,
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "pre_close": closes,
            "vol": 100_000,
            "amount": 1_000_000,
            "revision": 1,
            "available_at": "2026-08-10T16:00:00+08:00",
            "source": "test",
            "content_hash": [str(index) for index in range(len(dates))],
        }
    )
    daily = benchmark.copy()
    daily["ts_code"] = "000001.SZ"
    return daily, benchmark, dates


def _snapshot() -> tuple[ResearchPitSnapshot, list[str]]:
    daily, benchmark, dates = _frames()
    snapshot = ResearchPitSnapshot(
        decision_at="2026-08-10T16:00:00+08:00",
        data_start=dates[0],
        data_end=dates[-1],
        universe=("000001.SZ",),
        universe_sha256="a" * 64,
        dataset_fingerprint="b" * 16,
        daily=daily,
        benchmark_code="000300.SH",
        benchmark_sha256="c" * 64,
        benchmark_daily=benchmark,
    )
    return snapshot, dates


def test_regime_filter_allows_neutral_and_blocks_defense_causally() -> None:
    snapshot, dates = _snapshot()

    result = build_research_regime_filter(snapshot, start=dates[24], end=dates[25])

    assert dates[24] in result.allowed_signal_dates
    assert dates[25] in result.blocked_signal_dates
    identity = result.identity()
    assert identity["required_dates"] == 2
    assert identity["allowed_dates"] == 1
    assert identity["blocked_dates"] == 1
    assert identity["benchmark_sha256"] == "c" * 64
    assert len(identity["identity_sha256"]) == 64


def test_future_benchmark_changes_do_not_change_earlier_regime_decision() -> None:
    snapshot, dates = _snapshot()
    before = build_research_regime_filter(snapshot, start=dates[24], end=dates[24])
    changed = snapshot.benchmark_daily.copy()
    changed.loc[changed["trade_date"] > dates[24], "close"] = 1.0
    future_changed = ResearchPitSnapshot(
        **{
            **snapshot.__dict__,
            "benchmark_daily": changed,
        }
    )

    after = build_research_regime_filter(future_changed, start=dates[24], end=dates[24])

    assert before.allowed_signal_dates == after.allowed_signal_dates == frozenset({dates[24]})


def test_missing_benchmark_trade_date_fails_closed() -> None:
    snapshot, dates = _snapshot()
    missing = snapshot.benchmark_daily[snapshot.benchmark_daily["trade_date"] != dates[25]].copy()
    broken = ResearchPitSnapshot(**{**snapshot.__dict__, "benchmark_daily": missing})

    with pytest.raises(ResearchPitError, match="未覆盖研究交易日"):
        build_research_regime_filter(broken, start=dates[24], end=dates[25])


def test_optimizer_filters_signal_dates_before_replay(monkeypatch) -> None:
    dates = pd.bdate_range("2026-01-01", periods=80).strftime("%Y%m%d").tolist()
    market = pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "trade_date": dates,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "vol": 100_000,
        }
    )
    captured: list[list[str]] = []
    monkeypatch.setattr(
        optimizer,
        "_detect_signals_for_code",
        lambda *_args, **_kwargs: [
            {"day": dates[60], "entry_i": 60, "bench_vols": {1.5: 100.0}},
            {"day": dates[61], "entry_i": 61, "bench_vols": {1.5: 100.0}},
        ],
    )

    def fake_replay(_df, signals, _combos):
        captured.append([str(row["day"]) for row in signals])
        return {}

    monkeypatch.setattr(optimizer, "_replay_params", fake_replay)
    combo = {
        "strategy": "A",
        "vol_ratio_min": 1.5,
        "strong_reset": 3,
        "exit_window": 10,
        "stop_pct": 0.07,
    }

    optimizer._worker_chunk(
        (
            ["000001.SZ"],
            market,
            dates,
            dates,
            160,
            "A",
            [1.5],
            [combo],
            None,
            None,
            frozenset({dates[60]}),
        )
    )

    assert captured == [[dates[60]]]


def test_random_baseline_uses_same_allowed_signal_dates(monkeypatch) -> None:
    daily, _benchmark, dates = _frames()
    captured: list[dict] = []
    monkeypatch.setattr(
        "ab_screener.research.baselines._apply_portfolio_metrics",
        lambda _out, candidates, _daily, _policy: captured.extend(candidates),
    )

    random_baseline_trades(
        daily,
        n_trades=20,
        hold_days=2,
        allowed_signal_dates=frozenset({dates[24]}),
    )

    assert captured
    assert {row["date"] for row in captured} == {dates[24]}


def test_authoritative_request_rejects_regime_identity_mismatch(monkeypatch, tmp_path) -> None:
    snapshot, dates = _snapshot()
    expected = ResearchRegimeFilter(
        allowed_signal_dates=frozenset({dates[24]}),
        blocked_signal_dates=frozenset({dates[25]}),
        evidence={"version": "research-market-regime-v1.0.0", "identity_sha256": "d" * 64},
    )
    monkeypatch.setattr(
        "ab_screener.research.trusted_run.prepare_trusted_pit_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        "ab_screener.research.trusted_run.prepare_trusted_regime_filter",
        lambda *_args, **_kwargs: expected,
    )
    windows = {
        "is_start": dates[24],
        "is_end": dates[24],
        "oos_start": dates[25],
        "oos_end": dates[25],
        "mode": "full",
        "automatic_window": True,
        "wf_windows": [],
    }

    with pytest.raises(ValueError, match="市场状态门禁"):
        execute_trusted_research(
            research_run_id="mismatch",
            request={
                "strategy": "A",
                "mode": "grid",
                "max_codes": 20,
                "pit_snapshot": snapshot.identity(),
                "market_regime_filter": {"version": "tampered"},
            },
            windows=windows,
            db_path=tmp_path / "unused.db",
            code_version="code",
            dataset_version=snapshot.dataset_fingerprint,
            phase_cb=lambda *_args: None,
        )
