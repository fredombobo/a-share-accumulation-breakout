"""T06 席位特征：PIT as-of、小样本 fail-closed、顺序/修订确定性。"""
from __future__ import annotations

from ab_screener.features.lhb_features import (
    FEATURE_MODEL_VERSION,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    LhbSeatFact,
    compute_seat_features,
)


def _fact(**over: object) -> LhbSeatFact:
    base = {
        "seat_id": "seat-a",
        "actor_id": "actor-a",
        "ts_code": "000001.SZ",
        "trade_date": "20260801",
        "available_at": "2026-08-01T16:00:00+08:00",
        "revision": 1,
        "buy_fen": 1_000_000,
        "sell_fen": 0,
        "net_fen": 1_000_000,
        "industry": "电子",
        "float_mv_yuan": 8_000_000_000.0,
        "turnover": 8.0,
        "board_height": 1,
        "vs_ma20": 0.05,
        "event_id": "e1",
    }
    base.update(over)
    return LhbSeatFact(**base)  # type: ignore[arg-type]


def _window_facts(n: int, *, seat_id: str = "seat-a") -> list[LhbSeatFact]:
    rows: list[LhbSeatFact] = []
    for i in range(n):
        day = f"202608{i + 1:02d}"
        rows.append(
            _fact(
                seat_id=seat_id,
                trade_date=day,
                available_at=f"2026-08-{i + 1:02d}T16:00:00+08:00",
                event_id=f"e{i}",
                ts_code=f"00000{i % 3}.SZ",
            )
        )
    return rows


def test_insufficient_sample_is_not_zero_filled():
    out = compute_seat_features(
        _window_facts(2),
        seat_id="seat-a",
        as_of="2026-08-20T16:00:00+08:00",
        as_of_date="20260820",
        window_days=20,
    )
    assert out["status"] == STATUS_INSUFFICIENT
    assert out["features"] is None
    assert out["sample_size"] == 2


def test_features_ignore_facts_after_as_of():
    facts = _window_facts(8)
    late = _fact(
        trade_date="20260815",
        available_at="2026-08-21T09:00:00+08:00",
        revision=9,
        event_id="late",
        buy_fen=99_000_000,
        net_fen=99_000_000,
    )
    out = compute_seat_features(
        facts + [late],
        seat_id="seat-a",
        as_of="2026-08-20T16:00:00+08:00",
        as_of_date="20260820",
        window_days=20,
    )
    assert out["status"] == STATUS_OK
    assert out["sample_size"] == 8
    assert out["features"]["buy_yuan"] < 90_000.0


def test_revision_asof_picks_visible_version_only():
    old = _fact(revision=1, buy_fen=1_000_000, net_fen=1_000_000, available_at="2026-08-01T16:00:00+08:00")
    new = _fact(revision=2, buy_fen=9_000_000, net_fen=9_000_000, available_at="2026-08-10T16:00:00+08:00")
    filler = _window_facts(6)
    early = compute_seat_features(
        filler + [old, new],
        seat_id="seat-a",
        as_of="2026-08-05T16:00:00+08:00",
        as_of_date="20260808",
        window_days=20,
    )
    late = compute_seat_features(
        filler + [old, new],
        seat_id="seat-a",
        as_of="2026-08-20T16:00:00+08:00",
        as_of_date="20260820",
        window_days=20,
    )
    assert early["status"] == STATUS_OK
    assert late["status"] == STATUS_OK
    assert early["features"]["buy_yuan"] < late["features"]["buy_yuan"]


def test_feature_hash_stable_under_shuffle_and_duplicate_rows():
    facts = _window_facts(8)
    dup = list(reversed(facts)) + facts
    a = compute_seat_features(
        facts, seat_id="seat-a", as_of="2026-08-20T16:00:00+08:00", as_of_date="20260820", window_days=20
    )
    b = compute_seat_features(
        dup, seat_id="seat-a", as_of="2026-08-20T16:00:00+08:00", as_of_date="20260820", window_days=20
    )
    assert a["status"] == STATUS_OK
    assert a["content_hash"] == b["content_hash"]
    assert a["model_version"] == FEATURE_MODEL_VERSION
    assert a["features"]["direction"] == 1.0
