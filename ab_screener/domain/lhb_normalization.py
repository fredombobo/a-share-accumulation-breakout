"""龙虎榜原因 / 期间 / 资金指纹标准化（T04）。"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ab_screener.domain.data_point import canonical_json
from ab_screener.domain.lhb_contracts import (
    REASON_CATALOG_VERSION,
    LhbContractError,
    parse_trade_date,
    resolve_period,
)


@dataclass(frozen=True)
class ReasonHit:
    reason_code: str
    window_code: str
    reason_raw: str
    catalog_version: str = REASON_CATALOG_VERSION


@dataclass(frozen=True)
class ReasonRule:
    reason_code: str
    window_code: str
    all_of: tuple[str, ...]
    none_of: tuple[str, ...] = ()


# 更具体的规则在前，避免三日累计被当成单日。
REASON_RULES_V1: tuple[ReasonRule, ...] = (
    ReasonRule("SEVERE_ABNORMAL_30D", "D30", ("严重异常", "30")),
    ReasonRule("SEVERE_ABNORMAL_30D", "D30", ("严重异常", "三十")),
    ReasonRule("SEVERE_ABNORMAL_10D", "D10", ("严重异常", "10")),
    ReasonRule("SEVERE_ABNORMAL_10D", "D10", ("严重异常", "十日")),
    ReasonRule("PCT_DEV_UP_3D", "D3", ("连续三", "涨幅偏离")),
    ReasonRule("PCT_DEV_DOWN_3D", "D3", ("连续三", "跌幅偏离")),
    ReasonRule("AMPLITUDE_3D", "D3", ("连续三", "振幅")),
    ReasonRule("IPO_FIRST_DAY", "D1", ("上市首日",)),
    ReasonRule("IPO_FIRST_DAY", "D1", ("新股上市",)),
    ReasonRule("TURNOVER_1D", "D1", ("换手",)),
    ReasonRule("AMPLITUDE_1D", "D1", ("振幅",), ("连续三",)),
    ReasonRule("PCT_DEV_DOWN_1D", "D1", ("跌幅偏离",), ("连续三",)),
    ReasonRule("PCT_DEV_UP_1D", "D1", ("涨幅偏离",), ("连续三",)),
    ReasonRule("PCT_DEV_DOWN_1D", "D1", ("日跌幅",), ("连续三",)),
    ReasonRule("PCT_DEV_UP_1D", "D1", ("日涨幅",), ("连续三",)),
)

_THREE_DAY = re.compile(r"(?:连续)?\s*(?:3|三)\s*个?交易日")


def _has_three_day_window(text: str) -> bool:
    return _THREE_DAY.search(text) is not None


def classify_reason(reason_raw: str) -> ReasonHit:
    text = (reason_raw or "").strip()
    if not text:
        return ReasonHit("UNKNOWN", "UNRESOLVED_WINDOW", reason_raw or "")
    compact = re.sub(r"\s+", "", text)
    if "严重异常" in compact:
        if "30" in compact or "三十" in compact:
            return ReasonHit("SEVERE_ABNORMAL_30D", "D30", text)
        if "10" in compact or "十日" in compact or "十个交易日" in compact:
            return ReasonHit("SEVERE_ABNORMAL_10D", "D10", text)
    if _has_three_day_window(compact):
        if "涨跌幅偏离" in compact:
            return ReasonHit("PCT_DEV_BOTH_3D", "D3", text)
        if "跌幅偏离" in compact:
            return ReasonHit("PCT_DEV_DOWN_3D", "D3", text)
        if "涨幅偏离" in compact:
            return ReasonHit("PCT_DEV_UP_3D", "D3", text)
        if "振幅" in compact:
            return ReasonHit("AMPLITUDE_3D", "D3", text)
    if "非上市首日" not in compact and ("上市首日" in compact or "新股上市" in compact):
        return ReasonHit("IPO_FIRST_DAY", "D1", text)
    if "换手" in compact:
        return ReasonHit("TURNOVER_1D", "D1", text)
    if "振幅" in compact:
        return ReasonHit("AMPLITUDE_1D", "D1", text)
    if "跌幅偏离" in compact:
        return ReasonHit("PCT_DEV_DOWN_1D", "D1", text)
    if "涨幅偏离" in compact:
        return ReasonHit("PCT_DEV_UP_1D", "D1", text)
    if re.search(r"(?:日|当日|收盘价|收盘价格).*跌幅(?:达|达到)", compact):
        return ReasonHit("PRICE_DOWN_1D", "D1", text)
    if re.search(r"(?:日|当日|收盘价|收盘价格).*涨幅(?:达|达到)", compact):
        return ReasonHit("PRICE_UP_1D", "D1", text)
    for rule in REASON_RULES_V1:
        if all(token in text for token in rule.all_of) and not any(
            token in text for token in rule.none_of
        ):
            return ReasonHit(rule.reason_code, rule.window_code, text)
    return ReasonHit("UNKNOWN", "UNRESOLVED_WINDOW", text)


def lookback_bounds(disclose_date: str, n: int, calendar: Iterable[str]) -> tuple[str, str] | None:
    disclose = parse_trade_date(disclose_date)
    dates = sorted({parse_trade_date(d) for d in calendar if parse_trade_date(d) <= disclose})
    if len(dates) < n:
        return None
    window = dates[-n:]
    return window[0], window[-1]


def period_for_hit(
    hit: ReasonHit,
    disclose_date: str,
    *,
    calendar: Iterable[str] | None = None,
) -> tuple[str, str | None, str | None]:
    """返回 (window_code, period_start, period_end)。缺日历的累计窗不猜日期。"""
    disclose = parse_trade_date(disclose_date)
    if hit.window_code == "D1":
        start, end = resolve_period("D1", disclose)
        return "D1", start, end
    if hit.window_code == "UNRESOLVED_WINDOW":
        return "UNRESOLVED_WINDOW", None, None
    need = {"D3": 3, "D10": 10, "D30": 30}[hit.window_code]
    if not calendar:
        return "UNRESOLVED_WINDOW", None, None
    bounds = lookback_bounds(disclose, need, calendar)
    if bounds is None:
        return "UNRESOLVED_WINDOW", None, None
    return hit.window_code, bounds[0], bounds[1]


def flow_fingerprint(
    *,
    ts_code: str,
    window_code: str,
    period_start: str | None,
    period_end: str | None,
    seat_legs: list[tuple[str, int, int]],
    reason_raw: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "period_end": period_end,
        "period_start": period_start,
        "seats": sorted(seat_legs),
        "ts_code": ts_code,
        "window_code": window_code,
    }
    if window_code == "UNRESOLVED_WINDOW":
        payload["reason_raw"] = reason_raw or ""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:32]


def amount_coherence(
    *,
    seat_net_yuan: float,
    published_net_yuan: float | None,
    turnover_yuan: float | None,
) -> dict[str, Any]:
    """解释性质量检查，不要求恒等式。"""
    notes: list[str] = []
    status = "OK"
    if turnover_yuan is not None and turnover_yuan > 0 and abs(seat_net_yuan) > turnover_yuan * 1.2:
        status = "WARN"
        notes.append("席位净额显著大于当日成交额")
    if published_net_yuan is not None and abs(published_net_yuan) > 0:
        gap = abs(seat_net_yuan - published_net_yuan) / max(abs(published_net_yuan), 1.0)
        if gap > 0.25:
            status = "WARN"
            notes.append("席位净额与榜单公布合计偏差超过25%")
    if published_net_yuan is None and turnover_yuan is None:
        status = "UNRESOLVED"
        notes.append("缺少公布合计或成交额，无法核对")
    return {
        "status": status,
        "seat_net_yuan": seat_net_yuan,
        "published_net_yuan": published_net_yuan,
        "turnover_yuan": turnover_yuan,
        "notes": notes,
    }


def assert_unresolved_has_no_bounds(window_code: str, period_start: str | None, period_end: str | None) -> None:
    if window_code == "UNRESOLVED_WINDOW" and (period_start or period_end):
        raise LhbContractError("无法解析期间时不得猜测日期")
