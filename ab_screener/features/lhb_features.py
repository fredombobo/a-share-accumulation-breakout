"""龙虎榜席位滚动特征（T06）。只使用 available_at <= as_of 的事实。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ab_screener.domain.data_point import content_hash_for
from ab_screener.domain.lhb_contracts import (
    FEATURE_WINDOWS,
    fen_to_yuan,
    parse_trade_date,
    require_available_at,
)

FEATURE_MODEL_VERSION = "lhb-features-v1"
STATUS_OK = "OK"
STATUS_INSUFFICIENT = "INSUFFICIENT_SAMPLE"
MIN_SAMPLE_BY_WINDOW = {20: 5, 60: 8, 120: 12, 250: 20}
HALF_LIFE_FRACTION = 0.5
SMALL_CAP_YUAN = 5_000_000_000.0
MID_CAP_YUAN = 20_000_000_000.0


@dataclass(frozen=True)
class LhbSeatFact:
    seat_id: str
    actor_id: str
    ts_code: str
    trade_date: str
    available_at: str
    revision: int
    buy_fen: int
    sell_fen: int
    net_fen: int
    industry: str | None = None
    float_mv_yuan: float | None = None
    turnover: float | None = None
    board_height: int | None = None
    vs_ma20: float | None = None
    event_id: str = ""

    def business_key(self) -> tuple[str, ...]:
        return (self.seat_id, self.ts_code, self.trade_date, self.event_id)


def select_pit_facts(facts: list[LhbSeatFact], *, as_of: str) -> list[LhbSeatFact]:
    """同一业务键取 as_of 时可见的最大 revision。"""
    cutoff = require_available_at(as_of)
    usable = [f for f in facts if require_available_at(f.available_at) <= cutoff]
    best: dict[tuple[str, ...], LhbSeatFact] = {}
    for fact in usable:
        key = fact.business_key()
        prev = best.get(key)
        if prev is None or fact.revision > prev.revision:
            best[key] = fact
            continue
        if fact.revision == prev.revision and fact.available_at > prev.available_at:
            best[key] = fact
    return sorted(
        best.values(),
        key=lambda item: (item.trade_date, item.seat_id, item.ts_code, item.event_id, item.revision),
    )


def _window_dates(facts: list[LhbSeatFact], *, as_of_date: str, window_days: int) -> set[str]:
    as_of = parse_trade_date(as_of_date)
    dates = sorted({fact.trade_date for fact in facts if fact.trade_date <= as_of})
    return set(dates[-window_days:])


def _decay_weight(trade_date: str, dates_sorted: list[str]) -> float:
    if not dates_sorted:
        return 1.0
    idx = dates_sorted.index(trade_date)
    age = (len(dates_sorted) - 1) - idx
    half = max(len(dates_sorted) * HALF_LIFE_FRACTION, 1.0)
    return math.exp(-math.log(2.0) * age / half)


def _cap_bucket(float_mv_yuan: float | None) -> str | None:
    if float_mv_yuan is None:
        return None
    if float_mv_yuan < SMALL_CAP_YUAN:
        return "small"
    if float_mv_yuan < MID_CAP_YUAN:
        return "mid"
    return "large"


def _share(counter: dict[str, float], total: float) -> dict[str, float]:
    if total <= 0:
        return {}
    return {key: counter[key] / total for key in sorted(counter)}


def compute_seat_features(
    facts: list[LhbSeatFact],
    *,
    seat_id: str,
    as_of: str,
    as_of_date: str,
    window_days: int,
) -> dict[str, Any]:
    if window_days not in FEATURE_WINDOWS:
        raise ValueError(f"非法特征窗口: {window_days}")
    pit = [f for f in select_pit_facts(facts, as_of=as_of) if f.seat_id == seat_id]
    dates = _window_dates(pit, as_of_date=as_of_date, window_days=window_days)
    window_facts = [f for f in pit if f.trade_date in dates]
    min_n = MIN_SAMPLE_BY_WINDOW[window_days]
    if len(window_facts) < min_n:
        return {
            "status": STATUS_INSUFFICIENT,
            "seat_id": seat_id,
            "window_days": window_days,
            "as_of": as_of,
            "as_of_date": as_of_date,
            "sample_size": len(window_facts),
            "min_sample": min_n,
            "model_version": FEATURE_MODEL_VERSION,
            "features": None,
        }
    dates_sorted = sorted(dates)
    wsum = 0.0
    buy = sell = net_abs = 0.0
    turnover_w = 0.0
    turnover_n = 0.0
    first_w = consec_w = board_n = 0.0
    trend_w = reverse_w = vs_n = 0.0
    industries: dict[str, float] = {}
    caps: dict[str, float] = {}
    buckets = [0, 0, 0, 0]
    span = max(len(dates_sorted), 1)
    for fact in window_facts:
        weight = _decay_weight(fact.trade_date, dates_sorted)
        wsum += weight
        buy += fact.buy_fen * weight
        sell += fact.sell_fen * weight
        net_abs += abs(fact.net_fen) * weight
        if fact.industry:
            industries[fact.industry] = industries.get(fact.industry, 0.0) + weight
        bucket = _cap_bucket(fact.float_mv_yuan)
        if bucket:
            caps[bucket] = caps.get(bucket, 0.0) + weight
        if fact.turnover is not None:
            turnover_w += fact.turnover * weight
            turnover_n += weight
        if fact.board_height is not None:
            board_n += weight
            if fact.board_height <= 1:
                first_w += weight
            else:
                consec_w += weight
        if fact.vs_ma20 is not None:
            vs_n += weight
            if fact.vs_ma20 >= 0:
                trend_w += weight
            else:
                reverse_w += weight
        pos = dates_sorted.index(fact.trade_date)
        buckets[min(3, pos * 4 // span)] = 1
    gross = buy + sell
    features = {
        "sample_size": len(window_facts),
        "scale_yuan": float(fen_to_yuan(int(round(net_abs / wsum)))) if wsum else 0.0,
        "buy_yuan": float(fen_to_yuan(int(round(buy)))),
        "sell_yuan": float(fen_to_yuan(int(round(sell)))),
        "direction": (buy / gross) if gross else 0.0,
        "frequency": len(window_facts) / float(window_days),
        "purity": (net_abs / gross) if gross else 0.0,
        "persistence": sum(buckets) / 4.0,
        "industry_share": _share(industries, sum(industries.values())),
        "cap_share": _share(caps, sum(caps.values())),
        "turnover_mean": (turnover_w / turnover_n) if turnover_n else None,
        "first_board_share": (first_w / board_n) if board_n else None,
        "consecutive_board_share": (consec_w / board_n) if board_n else None,
        "trend_share": (trend_w / vs_n) if vs_n else None,
        "reversal_share": (reverse_w / vs_n) if vs_n else None,
    }
    payload = {
        "status": STATUS_OK,
        "seat_id": seat_id,
        "window_days": window_days,
        "as_of": as_of,
        "as_of_date": as_of_date,
        "sample_size": len(window_facts),
        "min_sample": min_n,
        "model_version": FEATURE_MODEL_VERSION,
        "features": features,
    }
    payload["content_hash"] = content_hash_for(features)
    return payload
