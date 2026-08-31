"""T07 画像：小样本收缩、口径隔离、金额可复算、下一开盘未成交。"""
from __future__ import annotations

from ab_screener.application.lhb_profiles import (
    FILLED,
    UNFILLABLE,
    ProfileEvent,
    build_profile,
    jeffreys_win_rate,
    next_open_return,
    reconcilable_net,
    wilson_center,
)
from ab_screener.domain.lhb_contracts import fen_to_yuan


def _ev(**over: object) -> ProfileEvent:
    base: dict[str, object] = {
        "event_id": "e1",
        "subject_id": "seat-a",
        "subject_type": "seat",
        "trade_date": "20260801",
        "ts_code": "000001.SZ",
        "buy_fen": 1_000_000,
        "sell_fen": 0,
        "net_fen": 1_000_000,
        "fill_status": FILLED,
        "horizon_returns": {1: 0.02},
    }
    base.update(over)
    return ProfileEvent(**base)  # type: ignore[arg-type]


def test_n3_all_win_is_not_unsrunken_100pct():
    events = [
        _ev(event_id=f"e{i}", trade_date=f"2026080{i}", horizon_returns={1: 0.01})
        for i in range(1, 4)
    ]
    out = build_profile(
        events, subject_type="seat", subject_id="seat-a", window_days=20, as_of_date="20260820"
    )
    assert out["sample_size"] == 3
    assert out["raw_win_rate"] == 1.0
    assert out["reliable_100pct_forbidden"] is True
    assert out["shrunk_win_rate"] < 1.0
    assert out["jeffreys_win_rate"] < 1.0
    assert out["display_win_rate"] < 1.0
    center, lo, hi = wilson_center(3, 3)
    assert center < 1.0
    assert 0.0 <= lo <= hi <= 1.0
    assert jeffreys_win_rate(3, 3) == 0.875
    assert out["win_rate_ci"]["center"] == center
    assert out["window_days"] == 20
    assert out["last_event_date"] == "20260803"


def test_amounts_drilldown_reconcilable():
    events = [
        _ev(event_id="e1", buy_fen=200, sell_fen=50, net_fen=150),
        _ev(event_id="e2", buy_fen=100, sell_fen=20, net_fen=80, trade_date="20260802"),
    ]
    out = build_profile(
        events, subject_type="seat", subject_id="seat-a", window_days=20, as_of_date="20260820"
    )
    assert out["amount_reconcilable"] is True
    assert reconcilable_net(events, out["event_ids"]) == 230
    assert out["net_yuan"] == float(fen_to_yuan(230))
    assert out["buy_yuan"] == float(fen_to_yuan(300))


def test_seat_actor_stock_profiles_are_not_mixed():
    events = [
        _ev(subject_type="seat", subject_id="seat-a", event_id="s1", net_fen=100),
        _ev(subject_type="actor", subject_id="actor-a", event_id="a1", net_fen=999),
        _ev(subject_type="stock", subject_id="000001.SZ", event_id="k1", net_fen=50),
    ]
    seat = build_profile(
        events, subject_type="seat", subject_id="seat-a", window_days=20, as_of_date="20260820"
    )
    actor = build_profile(
        events, subject_type="actor", subject_id="actor-a", window_days=20, as_of_date="20260820"
    )
    stock = build_profile(
        events, subject_type="stock", subject_id="000001.SZ", window_days=20, as_of_date="20260820"
    )
    assert seat["event_ids"] == ["s1"]
    assert actor["event_ids"] == ["a1"]
    assert stock["event_ids"] == ["k1"]
    assert seat["net_yuan"] != actor["net_yuan"]


def test_next_open_limit_up_is_unfillable():
    calendar = ["20260810", "20260811", "20260812"]
    bars = {
        "20260811": {"open": 11.0, "close": 11.0, "low": 11.0, "limit_up": 11.0},
        "20260812": {"open": 11.5, "close": 11.6, "low": 11.4, "limit_up": 12.1},
    }
    res = next_open_return(bars, signal_date="20260810", calendar=calendar, horizon=1)
    assert res["status"] == UNFILLABLE
    assert res["reason"] == "LIMIT_UP_OPEN"
    assert res["raw"] is None


def test_next_open_suspended_recorded_separately():
    calendar = ["20260810", "20260811"]
    bars = {"20260811": {"open": 10.0, "close": 10.1, "suspended": True}}
    res = next_open_return(bars, signal_date="20260810", calendar=calendar, horizon=1)
    assert res["status"] == "SUSPENDED"
    assert res["raw"] is None


def test_t_plus_one_and_limit_down_exit_are_enforced():
    calendar = ["20260810", "20260811", "20260812", "20260813"]
    bars = {
        "20260811": {"open": 10.0, "close": 10.2, "low": 9.9, "limit_up": 11.0},
        "20260812": {
            "open": 9.18,
            "high": 9.18,
            "low": 9.18,
            "close": 9.18,
            "limit_down": 9.18,
        },
        "20260813": {"open": 9.3, "high": 9.6, "low": 9.2, "close": 9.5},
    }
    res = next_open_return(bars, signal_date="20260810", calendar=calendar, horizon=1)
    assert res["status"] == FILLED
    assert res["entry_date"] == "20260811"
    assert res["exit_date"] == "20260813"
    assert res["exit_delayed_sessions"] == 1
    assert res["t_plus_one_enforced"] is True
