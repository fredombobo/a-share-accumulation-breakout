"""T06 风格概率、同 actor 去重投票、漂移检测。"""
from __future__ import annotations

from ab_screener.features.lhb_features import STATUS_INSUFFICIENT, STATUS_OK, LhbSeatFact
from ab_screener.research.seat_network import cooccurrence_edges, independent_flow_votes
from ab_screener.research.seat_style import (
    STYLE_LABELS,
    STYLE_MODEL_VERSION,
    classify_from_facts,
    classify_seat_style,
    detect_style_drift,
)


def _fact(**over: object) -> LhbSeatFact:
    base = {
        "seat_id": "seat-a",
        "actor_id": "actor-a",
        "ts_code": "000001.SZ",
        "trade_date": "20260801",
        "available_at": "2026-08-01T16:00:00+08:00",
        "revision": 1,
        "buy_fen": 2_000_000,
        "sell_fen": 0,
        "net_fen": 2_000_000,
        "industry": "电子",
        "float_mv_yuan": 6_000_000_000.0,
        "turnover": 18.0,
        "board_height": 1,
        "vs_ma20": 0.08,
        "event_id": "e1",
    }
    base.update(over)
    return LhbSeatFact(**base)  # type: ignore[arg-type]


def _series(*, n: int, board_height: int, vs_ma20: float, buy: int, sell: int, seat_id: str = "seat-a") -> list[LhbSeatFact]:
    rows: list[LhbSeatFact] = []
    for i in range(n):
        day = f"202607{i + 1:02d}" if i < 30 else f"202608{i - 29:02d}"
        rows.append(
            _fact(
                seat_id=seat_id,
                trade_date=day,
                available_at=f"2026-{day[4:6]}-{day[6:8]}T16:00:00+08:00",
                event_id=f"{seat_id}-{day}",
                board_height=board_height,
                vs_ma20=vs_ma20,
                buy_fen=buy,
                sell_fen=sell,
                net_fen=buy - sell,
                turnover=18.0 if board_height == 1 else 4.0,
            )
        )
    return rows


def test_style_probs_sum_to_one_and_carry_model_version():
    facts = _series(n=10, board_height=1, vs_ma20=0.1, buy=2_000_000, sell=0)
    out = classify_from_facts(
        facts, seat_id="seat-a", as_of="2026-08-20T16:00:00+08:00", as_of_date="20260820", window_days=20
    )
    assert out["status"] == STATUS_OK
    assert out["model_version"] == STYLE_MODEL_VERSION
    assert abs(sum(out["probs"].values()) - 1.0) < 1e-9
    assert set(out["probs"]) == set(STYLE_LABELS)
    assert out["probs"]["board_chase"] == max(out["probs"].values())


def test_insufficient_style_is_not_a_fake_distribution():
    facts = _series(n=2, board_height=1, vs_ma20=0.1, buy=1, sell=0)
    out = classify_from_facts(
        facts, seat_id="seat-a", as_of="2026-08-20T16:00:00+08:00", as_of_date="20260820", window_days=20
    )
    assert out["status"] == STATUS_INSUFFICIENT
    assert out["probs"] is None


def test_same_actor_two_seats_count_as_one_independent_vote():
    facts = [
        _fact(seat_id="s1", actor_id="hot-1", ts_code="000001.SZ", trade_date="20260803"),
        _fact(seat_id="s2", actor_id="hot-1", ts_code="000001.SZ", trade_date="20260803", event_id="e2"),
        _fact(seat_id="s3", actor_id="hot-2", ts_code="000001.SZ", trade_date="20260803", event_id="e3"),
    ]
    votes = independent_flow_votes(
        facts, ts_code="000001.SZ", trade_date="20260803", as_of="2026-08-03T16:00:00+08:00"
    )
    assert votes["seat_count"] == 3
    assert votes["independent_actors"] == 2
    edges = cooccurrence_edges(facts, as_of="2026-08-03T16:00:00+08:00")
    assert edges == [("hot-1", "hot-2", 1)]


def test_drift_detects_style_change_but_not_tiny_samples():
    board = _series(n=12, board_height=1, vs_ma20=0.2, buy=3_000_000, sell=0)
    tstyle = _series(n=12, board_height=3, vs_ma20=-0.1, buy=500_000, sell=2_500_000)
    early = classify_from_facts(
        board, seat_id="seat-a", as_of="2026-08-20T16:00:00+08:00", as_of_date="20260820", window_days=20
    )
    late = classify_from_facts(
        tstyle, seat_id="seat-a", as_of="2026-08-20T16:00:00+08:00", as_of_date="20260820", window_days=20
    )
    drifted = detect_style_drift(early, late)
    assert drifted["status"] == STATUS_OK
    assert drifted["alarm"] is True
    tiny = classify_seat_style({"status": STATUS_INSUFFICIENT, "sample_size": 2, "features": None})
    quiet = detect_style_drift(tiny, tiny)
    assert quiet["status"] == STATUS_INSUFFICIENT
    assert quiet["alarm"] is False
