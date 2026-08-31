"""T09 反未来函数、不可成交、试验全登记、研究门禁。"""
from __future__ import annotations

from ab_screener.application.lhb_signal_engine import load_policy
from ab_screener.domain.lhb_signal import SignalInput
from ab_screener.features.lhb_features import LhbSeatFact
from ab_screener.research.lhb_backtest import apply_costs, backtest_signals, generate_historical_signal
from ab_screener.research.lhb_validation import FAIL, INSUFFICIENT, append_trial, validate_research

CAL = ("20260810", "20260811", "20260812")


def _fact(**over: object) -> LhbSeatFact:
    base: dict[str, object] = {
        "seat_id": "seat-a",
        "actor_id": "actor-a",
        "ts_code": "000001.SZ",
        "trade_date": "20260810",
        "available_at": "2026-08-10T16:00:00+08:00",
        "revision": 1,
        "buy_fen": 10_000_000,
        "sell_fen": 0,
        "net_fen": 10_000_000,
        "event_id": "e1",
    }
    base.update(over)
    return LhbSeatFact(**base)  # type: ignore[arg-type]


def _inp(**over: object) -> SignalInput:
    base: dict[str, object] = {
        "ts_code": "000001.SZ",
        "disclose_date": "20260810",
        "disclose_at": "2026-08-10T16:00:00+08:00",
        "net_yuan": 0.0,
        "amount_yuan": 400_000_000.0,
        "adv20_yuan": 200_000_000.0,
        "purity": 0.8,
        "independent_actors": 3,
        "identity_confidence": 0.7,
        "identity_grade": "B",
        "turnover": 8.0,
        "data_complete": True,
        "next_bar_unfillable": False,
        "next_bar_suspended": False,
        "liquid": True,
        "crowded": False,
        "severe_abnormal": False,
        "calendar": CAL,
        "policy": load_policy(),
    }
    base.update(over)
    return SignalInput(**base)  # type: ignore[arg-type]


def test_current_identity_must_not_be_injected_into_old_asof():
    """游资名单/身份是 SignalInput 的 as-of 字段；不得把当前映射倒灌进历史日。"""
    facts = [_fact()]
    old = generate_historical_signal(
        facts, _inp(identity_confidence=0.2, identity_grade="C"), as_of="2026-08-10T16:00:00+08:00"
    )
    assert "IDENTITY_LOW_CONF" in old["vetoes"]
    assert old["identity_version"] == "i1"


def test_late_revision_does_not_change_historical_signal():
    early = _fact(net_fen=10_000_000, buy_fen=10_000_000, revision=1)
    late = _fact(
        net_fen=99_000_000,
        buy_fen=99_000_000,
        revision=2,
        available_at="2026-08-12T09:00:00+08:00",
    )
    hist = generate_historical_signal([early, late], _inp(), as_of="2026-08-10T16:00:00+08:00")
    future = generate_historical_signal([early, late], _inp(), as_of="2026-08-12T16:00:00+08:00")
    assert hist["feature_snapshot"]["net_yuan"] == 100_000.0
    assert future["feature_snapshot"]["net_yuan"] == 990_000.0
    assert hist["disclose_at"] == "2026-08-10T16:00:00+08:00"


def test_limit_up_sample_not_filled_at_open():
    signals = [{"ts_code": "000001.SZ", "disclose_date": "20260810"}]
    bars = {
        "000001.SZ": {
            "20260811": {"open": 11.0, "close": 11.0, "low": 11.0, "limit_up": 11.0},
        }
    }
    out = backtest_signals(signals, bars=bars, calendar=list(CAL))
    assert out["filled"] == 0
    assert out["unfillable"] == 1
    assert out["sample_size"] == 0


def test_backtest_report_has_required_fields():
    signals = [{"ts_code": "000001.SZ", "disclose_date": "20260810"}]
    bars = {
        "000001.SZ": {
            "20260811": {"open": 10.0, "close": 10.2, "low": 9.9, "limit_up": 11.0},
            "20260812": {"open": 10.2, "close": 10.4, "low": 10.1, "limit_up": 11.2},
        }
    }
    out = backtest_signals(signals, bars=bars, calendar=list(CAL))
    assert out["filled"] == 1
    assert out["gross_return"] > out["net_return"]
    assert apply_costs(0.02) < 0.02
    for key in ("gross_return", "net_return", "max_drawdown", "capacity_notional", "sample_size", "ci"):
        assert key in out
    assert "net_low" in out["ci"] and "net_high" in out["ci"]


def test_validation_insufficient_and_fail_cannot_claim_edge():
    low = validate_research(
        oos_net_excess=None, max_drawdown=None, sample_size=3, capacity_ok=True, anti_overfit_passed=True
    )
    assert low["verdict"] == INSUFFICIENT
    assert low["can_claim_edge"] is False
    bad = validate_research(
        oos_net_excess=-0.01,
        max_drawdown=-0.4,
        sample_size=80,
        capacity_ok=True,
        anti_overfit_passed=True,
        shadow_mature_events=80,
    )
    assert bad["verdict"] == FAIL
    assert bad["can_claim_edge"] is False
    overfit = validate_research(
        oos_net_excess=0.05,
        max_drawdown=-0.1,
        sample_size=80,
        capacity_ok=True,
        anti_overfit_passed=False,
        shadow_mature_events=80,
    )
    assert overfit["verdict"] == FAIL


def test_trial_registry_keeps_failures():
    registry: list[dict] = []
    append_trial(registry, {"params": {"a": 1}, "status": "FAILED", "outcome": -0.1})
    append_trial(registry, {"params": {"a": 2}, "status": "COMPLETED", "outcome": 0.02})
    assert len(registry) == 2
    assert {t["status"] for t in registry} == {"FAILED", "COMPLETED"}
