"""把原始 top_list / top_inst 行变成事件 + 金额事实 + 排名（T04）。"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ab_screener.domain.lhb_contracts import (
    REASON_CATALOG_VERSION,
    AmountUnit,
    LhbEventKey,
    LhbSeatLegs,
    LhbSeatRankFact,
    LhbSeatTradeFact,
    exchange_from_ts_code,
    fen_to_yuan,
    is_a_share_ts_code,
    materialize_seat_legs,
    normalize_top_inst_side,
    to_fen,
)
from ab_screener.domain.lhb_normalization import (
    ReasonHit,
    amount_coherence,
    classify_reason,
    flow_fingerprint,
    period_for_hit,
)


@dataclass(frozen=True)
class NormalizedEvent:
    key: LhbEventKey
    reason_raw: str
    period_start: str | None
    period_end: str | None
    flow_fingerprint: str
    source_status: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedDay:
    events: tuple[NormalizedEvent, ...]
    trades: tuple[LhbSeatTradeFact, ...]
    ranks: tuple[LhbSeatRankFact, ...]
    quality: dict[str, Any]


def _num(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法数值: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"非有限数值: {value!r}")
    return number


def _as_yuan(value: Any, unit: AmountUnit) -> float | None:
    if value in (None, ""):
        return None
    return float(fen_to_yuan(to_fen(value, unit)))


def _seat_ranks(inst_rows: list[dict[str, Any]]) -> dict[str, tuple[int | None, int | None]]:
    buyers = sorted(inst_rows, key=lambda r: _num(r.get("buy")), reverse=True)
    sellers = sorted(inst_rows, key=lambda r: _num(r.get("sell")), reverse=True)
    buy_rank: dict[str, int] = {}
    sell_rank: dict[str, int] = {}
    buy_i = 0
    sell_i = 0
    for row in buyers:
        if _num(row.get("buy")) <= 0:
            continue
        buy_i += 1
        buy_rank.setdefault(str(row.get("exalter")), buy_i)
    for row in sellers:
        if _num(row.get("sell")) <= 0:
            continue
        sell_i += 1
        sell_rank.setdefault(str(row.get("exalter")), sell_i)
    names = {str(r.get("exalter")) for r in inst_rows}
    return {name: (buy_rank.get(name), sell_rank.get(name)) for name in names}


def _inst_rows_for_reason(
    inst_rows: list[dict[str, Any]],
    event_hit: ReasonHit,
    *,
    disclose_date: str,
    calendar: Iterable[str] | None,
) -> list[dict[str, Any]]:
    """席位明细按上榜原因对齐；禁止把不同原因或 D1/D3 窗口混进同一资金事实。"""
    event_window, _, _ = period_for_hit(event_hit, disclose_date, calendar=calendar)
    matched: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for row in inst_rows:
        hit = classify_reason(str(row.get("reason") or ""))
        row_window, _, _ = period_for_hit(hit, disclose_date, calendar=calendar)
        if hit.reason_code == event_hit.reason_code and row_window == event_window:
            matched.append(row)
        elif hit.reason_code == "UNKNOWN":
            unknowns.append(row)
    if matched:
        return matched
    if event_window == "D1" and event_hit.window_code == "D1":
        return unknowns
    return []


def _merge_seat_amounts(inst_rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """同一席位买卖双榜两行：按 side 取值，禁止把重复行金额相加。"""
    merged: dict[str, dict[str, float]] = {}
    for row in inst_rows:
        name = str(row.get("exalter") or "")
        if not name:
            continue
        side = normalize_top_inst_side(row.get("side"), buy=row.get("buy"), sell=row.get("sell"))
        bucket = merged.setdefault(name, {"buy": 0.0, "sell": 0.0})
        if side == "BUY":
            bucket["buy"] = max(bucket["buy"], _num(row.get("buy")))
        elif side == "SELL":
            bucket["sell"] = max(bucket["sell"], _num(row.get("sell")))
        elif side == "BOTH":
            bucket["buy"] = max(bucket["buy"], _num(row.get("buy")))
            bucket["sell"] = max(bucket["sell"], _num(row.get("sell")))
        else:
            if bucket["buy"] == 0:
                bucket["buy"] = _num(row.get("buy"))
            if bucket["sell"] == 0:
                bucket["sell"] = _num(row.get("sell"))
    return merged


def _legs_for_stock(
    event_id: str,
    inst_rows: list[dict[str, Any]],
    *,
    available_at: str,
    source: str,
    unit: AmountUnit,
) -> list[LhbSeatLegs]:
    merged = _merge_seat_amounts(inst_rows)
    rank_rows = [
        {"exalter": name, "buy": amounts["buy"], "sell": amounts["sell"]}
        for name, amounts in merged.items()
    ]
    ranks = _seat_ranks(rank_rows)
    out: list[LhbSeatLegs] = []
    for name, amounts in sorted(merged.items()):
        buy_r, sell_r = ranks.get(name, (None, None))
        if buy_r is None and sell_r is None:
            continue
        out.append(
            materialize_seat_legs(
                event_id=event_id,
                seat_raw=name,
                buy_amount=amounts["buy"],
                sell_amount=amounts["sell"],
                unit=unit,
                buy_rank=buy_r,
                sell_rank=sell_r,
                available_at=available_at,
                source=source,
            )
        )
    return out


def transform_day(
    *,
    disclose_date: str,
    top_list_rows: list[dict[str, Any]],
    top_inst_rows: list[dict[str, Any]],
    available_at: str,
    source: str = "tushare",
    source_status: str = "COMPLETE",
    calendar: Iterable[str] | None = None,
    unit: AmountUnit | str = AmountUnit.YUAN,
    top_list_unit: AmountUnit | str = AmountUnit.YUAN,
) -> NormalizedDay:
    unit_e = AmountUnit(unit) if not isinstance(unit, AmountUnit) else unit
    list_unit_e = (
        AmountUnit(top_list_unit) if not isinstance(top_list_unit, AmountUnit) else top_list_unit
    )
    inst_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in top_inst_rows:
        inst_by_code.setdefault(str(row.get("ts_code")), []).append(row)

    events: list[NormalizedEvent] = []
    trades: list[LhbSeatTradeFact] = []
    ranks: list[LhbSeatRankFact] = []
    quality_by_code: dict[str, Any] = {}

    list_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in top_list_rows:
        ts_code = str(row.get("ts_code"))
        if is_a_share_ts_code(ts_code):
            list_by_code.setdefault(ts_code, []).append(row)

    for ts_code, list_rows in sorted(list_by_code.items()):
        exchange = exchange_from_ts_code(ts_code)
        inst_rows = inst_by_code.get(ts_code, [])
        prepared: list[tuple[NormalizedEvent, str, list[LhbSeatLegs]]] = []
        for row in list_rows:
            hit = classify_reason(str(row.get("reason") or ""))
            window_code, period_start, period_end = period_for_hit(
                hit, disclose_date, calendar=calendar
            )
            event_hit = ReasonHit(hit.reason_code, window_code, hit.reason_raw)
            event_key = LhbEventKey(
                exchange=exchange,
                ts_code=ts_code,
                window_code=window_code,
                reason_code=event_hit.reason_code,
                disclose_date=disclose_date,
            )
            matched_inst = _inst_rows_for_reason(
                inst_rows,
                event_hit,
                disclose_date=disclose_date,
                calendar=calendar,
            )
            legs = _legs_for_stock(
                event_key.event_id,
                matched_inst,
                available_at=available_at,
                source=source,
                unit=unit_e,
            )
            fp = flow_fingerprint(
                ts_code=ts_code,
                window_code=window_code,
                period_start=period_start,
                period_end=period_end,
                seat_legs=[(leg.trade.seat_raw, leg.trade.buy_fen, leg.trade.sell_fen) for leg in legs],
                reason_raw=event_hit.reason_raw if window_code == "UNRESOLVED_WINDOW" else None,
            )
            event = NormalizedEvent(
                key=event_key,
                reason_raw=event_hit.reason_raw,
                period_start=period_start,
                period_end=period_end,
                flow_fingerprint=fp,
                source_status=source_status,
                payload={
                    "reason_catalog_version": REASON_CATALOG_VERSION,
                    "l_amount": row.get("l_amount"),
                    "net_amount": row.get("net_amount"),
                    "amount": row.get("amount"),
                },
            )
            prepared.append((event, fp, legs))

        by_fp: dict[str, list[tuple[NormalizedEvent, list[LhbSeatLegs]]]] = {}
        for event, fp, legs in prepared:
            events.append(event)
            by_fp.setdefault(fp, []).append((event, legs))
        for fp, group in by_fp.items():
            primary_event, legs = min(group, key=lambda item: item[0].key.reason_code)
            primary_id = primary_event.key.event_id
            for leg in legs:
                trades.append(
                    LhbSeatTradeFact(
                        event_id=primary_id,
                        seat_raw=leg.trade.seat_raw,
                        buy_fen=leg.trade.buy_fen,
                        sell_fen=leg.trade.sell_fen,
                        net_fen=leg.trade.net_fen,
                        available_at=available_at,
                        source=source,
                    )
                )
                ranks.extend(
                    LhbSeatRankFact(
                        event_id=primary_id,
                        seat_raw=rank.seat_raw,
                        side=rank.side,
                        rank_no=rank.rank_no,
                        available_at=available_at,
                        source=source,
                    )
                    for rank in leg.ranks
                )
            seat_net_yuan = float(sum(fen_to_yuan(leg.trade.net_fen) for leg in legs))
            published = _as_yuan(primary_event.payload.get("net_amount"), list_unit_e)
            turnover = _as_yuan(primary_event.payload.get("amount"), list_unit_e)
            quality_by_code[f"{ts_code}:{primary_event.key.window_code}:{fp}"] = amount_coherence(
                seat_net_yuan=seat_net_yuan,
                published_net_yuan=published,
                turnover_yuan=turnover,
            )

    events_sorted = tuple(sorted(events, key=lambda e: (e.key.ts_code, e.key.reason_code, e.key.window_code)))
    return NormalizedDay(
        events=events_sorted,
        trades=tuple(trades),
        ranks=tuple(ranks),
        quality=quality_by_code,
    )
