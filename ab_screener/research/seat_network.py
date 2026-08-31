"""席位共现网络：同一 actor 只计一个独立主体（T06）。"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ab_screener.domain.lhb_contracts import parse_trade_date
from ab_screener.features.lhb_features import LhbSeatFact, select_pit_facts


def _day_actors(facts: list[LhbSeatFact]) -> dict[tuple[str, str], set[str]]:
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fact in facts:
        actor = fact.actor_id or fact.seat_id
        grouped[(fact.ts_code, fact.trade_date)].add(actor)
    return grouped


def independent_flow_votes(
    facts: list[LhbSeatFact],
    *,
    ts_code: str,
    trade_date: str,
    as_of: str,
) -> dict[str, Any]:
    """同一股票同一日的独立资金主体数：同 actor 多席位只计 1。"""
    parse_trade_date(trade_date)
    pit = select_pit_facts(facts, as_of=as_of)
    actors = {
        (fact.actor_id or fact.seat_id)
        for fact in pit
        if fact.ts_code == ts_code and fact.trade_date == trade_date
    }
    seats = {fact.seat_id for fact in pit if fact.ts_code == ts_code and fact.trade_date == trade_date}
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "seat_count": len(seats),
        "independent_actors": len(actors),
        "actor_ids": tuple(sorted(actors)),
    }


def cooccurrence_edges(
    facts: list[LhbSeatFact],
    *,
    as_of: str,
) -> list[tuple[str, str, int]]:
    pit = select_pit_facts(facts, as_of=as_of)
    weights: dict[tuple[str, str], int] = defaultdict(int)
    for actors in _day_actors(pit).values():
        ordered = sorted(actors)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                weights[(left, right)] += 1
    return [(a, b, w) for (a, b), w in sorted(weights.items())]
