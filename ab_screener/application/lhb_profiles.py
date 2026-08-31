"""席位 / actor / 股票 / 板块画像（T07）。小样本收缩，禁止展示 100% 可靠胜率。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from ab_screener.domain.lhb_contracts import fen_to_yuan, parse_trade_date
from ab_screener.features.lhb_features import LhbSeatFact, select_pit_facts

SubjectType = Literal["seat", "actor", "stock", "board"]
UNFILLABLE = "UNFILLABLE"
SUSPENDED = "SUSPENDED"
FILLED = "FILLED"


@dataclass(frozen=True)
class ProfileEvent:
    event_id: str
    subject_id: str
    subject_type: SubjectType
    trade_date: str
    ts_code: str
    buy_fen: int
    sell_fen: int
    net_fen: int
    industry: str | None = None
    float_mv_yuan: float | None = None
    turnover: float | None = None
    fill_status: str = FILLED
    horizon_returns: dict[int, float | None] = field(default_factory=dict)
    horizon_excess: dict[int, float | None] = field(default_factory=dict)


def wilson_center(wins: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """返回 (收缩中心, 下界, 上界)。n=3 全胜时中心 < 1。"""
    if n <= 0:
        return (float("nan"), float("nan"), float("nan"))
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denom
    return center, max(0.0, center - half), min(1.0, center + half)


def shrunk_win_rate(wins: int, n: int) -> float:
    """Laplace 收缩：(wins+1)/(n+2)。"""
    return (wins + 1.0) / (n + 2.0) if n >= 0 else float("nan")


def jeffreys_win_rate(wins: int, n: int) -> float:
    """Jeffreys 先验 Beta(0.5,0.5) 后验均值。n=3 全胜时 < 1。"""
    return (wins + 0.5) / (n + 1.0) if n >= 0 else float("nan")


def next_open_return(
    bars: dict[str, dict[str, Any]],
    *,
    signal_date: str,
    calendar: list[str],
    horizon: int,
    benchmark: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """下一交易日开盘入场；至少 T+1 后退出，跌停/停牌顺延。"""
    parse_trade_date(signal_date)
    if horizon < 1:
        raise ValueError("horizon 必须 >= 1")
    future = [d for d in calendar if d > signal_date]
    if not future:
        return {"status": UNFILLABLE, "reason": "NO_NEXT_SESSION", "raw": None, "excess": None}
    entry_date = future[0]
    entry = bars.get(entry_date)
    if entry is None or entry.get("suspended"):
        return {"status": SUSPENDED, "reason": "SUSPENDED", "raw": None, "excess": None}
    limit_up = float(entry.get("limit_up") or 0.0)
    if limit_up and float(entry["open"]) >= limit_up - 1e-9 and float(entry.get("low", entry["open"])) >= limit_up - 1e-9:
        return {"status": UNFILLABLE, "reason": "LIMIT_UP_OPEN", "raw": None, "excess": None}
    # future[0] 是买入日；A 股不能当日回转，h=1 最早 future[1] 卖出。
    if horizon >= len(future):
        return {"status": UNFILLABLE, "reason": "HORIZON_NOT_MATURE", "raw": None, "excess": None}
    exit_date: str | None = None
    exit_px: float | None = None
    delayed = 0
    last_reason = "HORIZON_NOT_MATURE"
    for candidate in future[horizon:]:
        exit_bar = bars.get(candidate)
        if exit_bar is None or exit_bar.get("suspended"):
            delayed += 1
            last_reason = "EXIT_SUSPENDED"
            continue
        limit_down = float(exit_bar.get("limit_down") or 0.0)
        if (
            limit_down
            and float(exit_bar.get("open") or 0.0) <= limit_down + 1e-9
            and float(exit_bar.get("high", exit_bar.get("open") or 0.0)) <= limit_down + 1e-9
        ):
            delayed += 1
            last_reason = "LIMIT_DOWN_EXIT"
            continue
        exit_date = candidate
        exit_px = float(exit_bar["close"])
        break
    if exit_date is None or exit_px is None:
        status = SUSPENDED if last_reason == "EXIT_SUSPENDED" else UNFILLABLE
        return {"status": status, "reason": last_reason, "raw": None, "excess": None}
    entry_px = float(entry["open"])
    if entry_px <= 0:
        return {"status": UNFILLABLE, "reason": "BAD_PRICE", "raw": None, "excess": None}
    raw = exit_px / entry_px - 1.0
    excess = None
    if benchmark is not None:
        b_in = benchmark.get(entry_date)
        b_out = benchmark.get(exit_date)
        if b_in and b_out and float(b_in.get("open") or 0) > 0:
            bex = float(b_out["close"]) / float(b_in["open"]) - 1.0
            excess = raw - bex
    return {
        "status": FILLED,
        "reason": None,
        "raw": raw,
        "excess": excess,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "exit_delayed_sessions": delayed,
        "t_plus_one_enforced": True,
    }


def build_profile(
    events: list[ProfileEvent],
    *,
    subject_type: SubjectType,
    subject_id: str,
    window_days: int,
    as_of_date: str,
) -> dict[str, Any]:
    rows = [e for e in events if e.subject_type == subject_type and e.subject_id == subject_id]
    # 未成熟结果不能作为 0% 胜率混进分母。金额/事件统计仍保留全部 rows。
    filled = [
        e for e in rows if e.fill_status == FILLED and e.horizon_returns.get(1) is not None
    ]
    wins = 0
    for event in filled:
        r1 = event.horizon_returns.get(1)
        if r1 is not None and r1 > 0:
            wins += 1
    n = len(filled)
    center, lo, hi = wilson_center(wins, n) if n else (float("nan"), float("nan"), float("nan"))
    shrunk = shrunk_win_rate(wins, n) if n else float("nan")
    jeffreys = jeffreys_win_rate(wins, n) if n else float("nan")
    buy = sum(e.buy_fen for e in rows)
    sell = sum(e.sell_fen for e in rows)
    net = sum(e.net_fen for e in rows)
    raw_wr = (wins / n) if n else None
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "window_days": window_days,
        "as_of_date": as_of_date,
        "sample_size": n,
        "event_count": len(rows),
        "last_event_date": max((e.trade_date for e in rows), default=None),
        "raw_win_rate": raw_wr,
        "shrunk_win_rate": shrunk if n else None,
        "jeffreys_win_rate": jeffreys if n else None,
        "display_win_rate": jeffreys if n else None,
        "reliable_100pct_forbidden": bool(raw_wr == 1.0 and n < 20),
        "win_rate_ci": {"low": lo, "high": hi, "center": center} if n else None,
        "buy_yuan": float(fen_to_yuan(buy)),
        "sell_yuan": float(fen_to_yuan(sell)),
        "net_yuan": float(fen_to_yuan(net)),
        "unfillable": sum(1 for e in rows if e.fill_status == UNFILLABLE),
        "suspended": sum(1 for e in rows if e.fill_status == SUSPENDED),
        "event_ids": [e.event_id for e in rows],
        "amount_reconcilable": True,
    }


def reconcilable_net(events: list[ProfileEvent], event_ids: list[str]) -> int:
    wanted = set(event_ids)
    return sum(e.net_fen for e in events if e.event_id in wanted)


def facts_to_events(facts: list[LhbSeatFact], *, as_of: str, subject_type: SubjectType) -> list[ProfileEvent]:
    pit = select_pit_facts(facts, as_of=as_of)
    out: list[ProfileEvent] = []
    for fact in pit:
        sid = {"seat": fact.seat_id, "actor": fact.actor_id, "stock": fact.ts_code, "board": fact.industry or "UNK"}[
            subject_type
        ]
        out.append(
            ProfileEvent(
                event_id=fact.event_id or f"{fact.seat_id}:{fact.trade_date}:{fact.ts_code}",
                subject_id=sid,
                subject_type=subject_type,
                trade_date=fact.trade_date,
                ts_code=fact.ts_code,
                buy_fen=fact.buy_fen,
                sell_fen=fact.sell_fen,
                net_fen=fact.net_fen,
                industry=fact.industry,
                float_mv_yuan=fact.float_mv_yuan,
                turnover=fact.turnover,
            )
        )
    return out
