"""龙虎榜领域契约（T01 冻结）。

本模块只定义口径、状态、键和金额换算，不抓取、不画像、不产生交易指令。
金额在进入领域层之前必须换成人民币「元」的分（fen）；表内存整数分，对外口径为元。
时间统一 Asia/Shanghai（+08:00）。身份输出只允许通道/候选假设，禁止断言自然人。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from ab_screener.domain.data_point import canonical_json, normalize_ts

TZ_NAME = "Asia/Shanghai"
CANONICAL_CURRENCY = "CNY"
CANONICAL_AMOUNT_UNIT = "yuan"
AMOUNT_SCALE_FEN = 100
WAN_YUAN_TO_YUAN = Decimal(10000)
REASON_CATALOG_VERSION = "v1"
CONTRACT_VERSION = "lhb-v1"

LHB_RESEARCH_ONLY = True
LHB_MAY_GENERATE_ORDERS = False

SOURCE_STATUS_VALUES = (
    "VALID_EMPTY",
    "NOT_PUBLISHED",
    "FETCH_FAILED",
    "DEGRADED",
    "COMPLETE",
)
WINDOW_CODE_VALUES = ("D1", "D3", "D10", "D30", "UNRESOLVED_WINDOW")
EXCHANGE_VALUES = ("SH", "SZ", "BJ")
RANK_SIDE_VALUES = ("BUY", "SELL")
RAW_SIDE_VALUES = ("BUY", "SELL", "BOTH", "UNK")
# Tushare top_inst.side：0 买入 / 1 卖出（接口返回字符串 '0'/'1'）。
_TUSHARE_SIDE_BUY = frozenset({"0", "0.0", "BUY", "B", "买", "买入"})
_TUSHARE_SIDE_SELL = frozenset({"1", "1.0", "SELL", "S", "卖", "卖出"})
OFFICIAL_TAG_VALUES = (
    "INSTITUTION_CHANNEL",
    "SH_CONNECT",
    "SZ_CONNECT",
    "HQ_NON_BRANCH",
    "BRANCH",
    "UNKNOWN",
)
ACTOR_TYPE_VALUES = (
    "INSTITUTION_CHANNEL",
    "CONNECT_CHANNEL",
    "HOT_MONEY_CANDIDATE",
    "BEHAVIORAL_MAIN_FORCE",
    "UNKNOWN",
)
EVIDENCE_GRADE_VALUES = ("A", "B", "C")
CONFLICT_STATUS_VALUES = ("NONE", "OPEN", "RESOLVED")
SIGNAL_STATUS_VALUES = (
    "WATCH",
    "CONFIRMED_FLOW",
    "RESEARCH_ENTRY",
    "NO_CHASE",
    "INVALIDATED",
)
RECON_DIFF_VALUES = (
    "MISSING_LEFT",
    "MISSING_RIGHT",
    "AMOUNT",
    "REASON",
    "WINDOW",
    "SEAT",
    "OTHER",
)
RECON_STATUS_VALUES = ("OPEN", "ACKNOWLEDGED", "ACCEPTED_AS_SOURCE_DIFF", "REJECTED")
OUTCOME_STATUS_VALUES = ("PENDING", "MATURED", "UNFILLABLE", "EXPIRED")
FEATURE_WINDOWS = (20, 60, 120, 250)
OUTCOME_HORIZONS = (1, 3, 5, 10, 20)
CONFIRMED_SIGNAL_STATUSES = ("CONFIRMED_FLOW", "RESEARCH_ENTRY")
HOT_MONEY_MAX_EVIDENCE_GRADE = "B"

REASON_CODES_V1 = (
    "PCT_DEV_UP_1D",
    "PCT_DEV_DOWN_1D",
    "PRICE_UP_1D",
    "PRICE_DOWN_1D",
    "AMPLITUDE_1D",
    "TURNOVER_1D",
    "PCT_DEV_UP_3D",
    "PCT_DEV_DOWN_3D",
    "PCT_DEV_BOTH_3D",
    "AMPLITUDE_3D",
    "SEVERE_ABNORMAL_10D",
    "SEVERE_ABNORMAL_30D",
    "IPO_FIRST_DAY",
    "UNKNOWN",
)

REASON_WINDOW_V1: dict[str, str] = {
    "PCT_DEV_UP_1D": "D1",
    "PCT_DEV_DOWN_1D": "D1",
    "PRICE_UP_1D": "D1",
    "PRICE_DOWN_1D": "D1",
    "AMPLITUDE_1D": "D1",
    "TURNOVER_1D": "D1",
    "IPO_FIRST_DAY": "D1",
    "PCT_DEV_UP_3D": "D3",
    "PCT_DEV_DOWN_3D": "D3",
    "PCT_DEV_BOTH_3D": "D3",
    "AMPLITUDE_3D": "D3",
    "SEVERE_ABNORMAL_10D": "D10",
    "SEVERE_ABNORMAL_30D": "D30",
    "UNKNOWN": "UNRESOLVED_WINDOW",
}

EVENT_KEY_FIELDS = ("exchange", "ts_code", "window_code", "reason_code", "disclose_date")


class LhbContractError(ValueError):
    """契约校验失败（fail-closed）。"""


class AmountUnit(str, Enum):
    YUAN = "yuan"
    WAN_YUAN = "wan_yuan"
    FEN = "fen"


TUSHARE_AMOUNT_UNITS: dict[str, dict[str, AmountUnit]] = {
    "top_list": {
        "amount": AmountUnit.YUAN,
        "l_sell": AmountUnit.YUAN,
        "l_buy": AmountUnit.YUAN,
        "l_amount": AmountUnit.YUAN,
        "net_amount": AmountUnit.YUAN,
        "float_values": AmountUnit.YUAN,
    },
    "top_inst": {
        "buy": AmountUnit.YUAN,
        "sell": AmountUnit.YUAN,
        "net_buy": AmountUnit.YUAN,
    },
}


def tushare_amount_unit(dataset: str, field: str) -> AmountUnit:
    """返回已冻结的 Tushare 字段单位；未知字段拒绝猜测。"""
    try:
        return TUSHARE_AMOUNT_UNITS[dataset][field]
    except KeyError as exc:
        raise LhbContractError(f"未声明的 Tushare 金额字段: {dataset}.{field}") from exc


def sql_enum(values: tuple[str, ...]) -> str:
    inner = ",".join("'" + item.replace("'", "''") + "'" for item in values)
    return f"({inner})"


def parse_trade_date(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise LhbContractError(f"非法日期: {value!r}")
    year, month, day = int(value[:4]), int(value[4:6]), int(value[6:8])
    try:
        date(year, month, day)
    except ValueError as exc:
        raise LhbContractError(f"非法日期: {value}") from exc
    return value


def require_available_at(value: Any) -> str:
    try:
        return normalize_ts(value)
    except ValueError as exc:
        raise LhbContractError(str(exc)) from exc


def parse_enum(value: str, allowed: tuple[str, ...], *, label: str) -> str:
    if value not in allowed:
        raise LhbContractError(f"未知状态: {value!r}（{label}）")
    return value


def normalize_top_inst_side(side: Any, *, buy: Any = None, sell: Any = None) -> str:
    """把 Tushare side（含 '0'/'1'）映射为 BUY/SELL/BOTH/UNK。"""
    raw = "" if side is None else str(side).strip()
    if raw in RAW_SIDE_VALUES:
        return raw
    token = raw.upper() if raw.isascii() else raw
    if raw in _TUSHARE_SIDE_BUY or token in _TUSHARE_SIDE_BUY:
        return "BUY"
    if raw in _TUSHARE_SIDE_SELL or token in _TUSHARE_SIDE_SELL:
        return "SELL"
    buy_v = 0.0
    sell_v = 0.0
    try:
        buy_v = float(buy or 0)
    except (TypeError, ValueError):
        buy_v = 0.0
    try:
        sell_v = float(sell or 0)
    except (TypeError, ValueError):
        sell_v = 0.0
    if buy_v > 0 and sell_v > 0:
        return "BOTH"
    if buy_v > 0:
        return "BUY"
    if sell_v > 0:
        return "SELL"
    return "UNK"


def exchange_from_ts_code(ts_code: str) -> str:
    if not isinstance(ts_code, str) or "." not in ts_code:
        raise LhbContractError(f"非法证券代码: {ts_code!r}")
    suffix = ts_code.rsplit(".", 1)[-1].upper()
    mapping = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}
    if suffix not in mapping:
        raise LhbContractError(f"未知交易所后缀: {ts_code!r}")
    return mapping[suffix]


def is_a_share_ts_code(ts_code: str) -> bool:
    """限制为沪深北 A 股，排除可转债、B 股、基金等同后缀证券。"""
    if not isinstance(ts_code, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", ts_code.upper()):
        return False
    code, suffix = ts_code.upper().split(".")
    if suffix == "SH":
        return code.startswith("6")
    if suffix == "SZ":
        return code.startswith(("0", "3"))
    return code.startswith(("4", "8", "9"))


def to_fen(value: Any, unit: AmountUnit | str) -> int:
    """把来源金额换算为整数分。无法精确到分则拒绝。"""
    unit_key = unit.value if isinstance(unit, AmountUnit) else str(unit)
    if unit_key not in {item.value for item in AmountUnit}:
        raise LhbContractError(f"未知金额单位: {unit_key!r}")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LhbContractError(f"非法金额: {value!r}") from exc
    if not amount.is_finite():
        raise LhbContractError(f"非法金额: {value!r}")
    if unit_key == AmountUnit.WAN_YUAN.value:
        fen = amount * WAN_YUAN_TO_YUAN * AMOUNT_SCALE_FEN
    elif unit_key == AmountUnit.YUAN.value:
        fen = amount * AMOUNT_SCALE_FEN
    else:
        fen = amount
    if fen != fen.to_integral_value():
        raise LhbContractError(f"金额精度超过分: {value!r} {unit_key}")
    return int(fen)


def fen_to_yuan(fen: int) -> Decimal:
    return (Decimal(fen) / Decimal(AMOUNT_SCALE_FEN)).quantize(Decimal("0.01"))


def validate_seat_amounts(*, buy_fen: int, sell_fen: int, net_fen: int) -> None:
    if buy_fen < 0 or sell_fen < 0:
        raise LhbContractError(f"负金额: buy={buy_fen} sell={sell_fen}")
    if buy_fen - sell_fen != net_fen:
        raise LhbContractError(
            f"买卖净额不一致: buy={buy_fen} sell={sell_fen} net={net_fen}"
        )


def window_for_reason(reason_code: str) -> str:
    parse_enum(reason_code, REASON_CODES_V1, label="reason_code")
    return REASON_WINDOW_V1[reason_code]


def resolve_period(
    window_code: str,
    disclose_date: str,
    *,
    period_start: str | None = None,
    period_end: str | None = None,
) -> tuple[str | None, str | None]:
    parse_enum(window_code, WINDOW_CODE_VALUES, label="window_code")
    disclose = parse_trade_date(disclose_date)
    if window_code == "UNRESOLVED_WINDOW":
        if period_start is not None or period_end is not None:
            raise LhbContractError("无法解析期间时不得猜测日期")
        return None, None
    if window_code == "D1":
        start = parse_trade_date(period_start) if period_start else disclose
        end = parse_trade_date(period_end) if period_end else disclose
        if start != disclose or end != disclose:
            raise LhbContractError("单日榜期间必须等于披露日")
        return start, end
    if not period_start or not period_end:
        raise LhbContractError("累计榜缺少期间，不得猜测日期")
    start = parse_trade_date(period_start)
    end = parse_trade_date(period_end)
    if start > end:
        raise LhbContractError(f"非法日期期间: {start}..{end}")
    return start, end


def event_id_for(
    *,
    exchange: str,
    ts_code: str,
    window_code: str,
    reason_code: str,
    disclose_date: str,
) -> str:
    payload = "|".join(
        (
            parse_enum(exchange, EXCHANGE_VALUES, label="exchange"),
            ts_code,
            parse_enum(window_code, WINDOW_CODE_VALUES, label="window_code"),
            parse_enum(reason_code, REASON_CODES_V1, label="reason_code"),
            parse_trade_date(disclose_date),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def assert_unique_event_ids(event_ids: list[str]) -> None:
    if len(event_ids) != len(set(event_ids)):
        raise LhbContractError("重复键")


def source_status_allows_confirmed(status: str) -> bool:
    parse_enum(status, SOURCE_STATUS_VALUES, label="source_status")
    return status == "COMPLETE"


def validate_manifest_row(*, source_status: str, row_count: int) -> None:
    parse_enum(source_status, SOURCE_STATUS_VALUES, label="source_status")
    if row_count < 0:
        raise LhbContractError(f"非法行数: {row_count}")
    if source_status == "VALID_EMPTY" and row_count != 0:
        raise LhbContractError("VALID_EMPTY 必须是已发布且零行")
    if source_status == "COMPLETE" and row_count == 0:
        raise LhbContractError("零行不得标为 COMPLETE，应使用 VALID_EMPTY")
    if source_status in {"NOT_PUBLISHED", "FETCH_FAILED"} and row_count != 0:
        raise LhbContractError(f"{source_status} 不得写入业务行伪装为空成功")


def identity_display(
    *,
    actor_type: str,
    label: str,
    evidence_grade: str,
) -> str:
    parse_enum(actor_type, ACTOR_TYPE_VALUES, label="actor_type")
    parse_enum(evidence_grade, EVIDENCE_GRADE_VALUES, label="evidence_grade")
    if actor_type == "INSTITUTION_CHANNEL":
        return "机构专用通道"
    if actor_type == "CONNECT_CHANNEL":
        return "沪深股通聚合通道"
    if actor_type == "HOT_MONEY_CANDIDATE":
        if evidence_grade == "A":
            raise LhbContractError("第三方游资映射不得标为 A 级证据")
        return f"疑似{label}（候选）"
    if actor_type == "BEHAVIORAL_MAIN_FORCE":
        return f"行为型主力（风格，非实名）:{label}"
    return "未知席位"


def reject_certain_person_claim(text: str) -> None:
    banned = ("确定为", "本人即", "实控人为")
    for token in banned:
        if token in text:
            raise LhbContractError(f"禁止断言具体自然人身份: {text}")


@dataclass(frozen=True)
class LhbEventKey:
    exchange: str
    ts_code: str
    window_code: str
    reason_code: str
    disclose_date: str

    def __post_init__(self) -> None:
        parse_enum(self.exchange, EXCHANGE_VALUES, label="exchange")
        if not self.ts_code or exchange_from_ts_code(self.ts_code) != self.exchange:
            raise LhbContractError(
                f"交易所与证券代码不一致: {self.exchange} {self.ts_code}"
            )
        parse_enum(self.window_code, WINDOW_CODE_VALUES, label="window_code")
        parse_enum(self.reason_code, REASON_CODES_V1, label="reason_code")
        parse_trade_date(self.disclose_date)
        if self.window_code == "UNRESOLVED_WINDOW":
            return
        expected_window = window_for_reason(self.reason_code)
        if self.reason_code != "UNKNOWN" and self.window_code != expected_window:
            raise LhbContractError(
                f"原因与统计期间不一致: {self.reason_code} -> {expected_window}"
            )

    @property
    def event_id(self) -> str:
        return event_id_for(
            exchange=self.exchange,
            ts_code=self.ts_code,
            window_code=self.window_code,
            reason_code=self.reason_code,
            disclose_date=self.disclose_date,
        )


@dataclass(frozen=True)
class LhbSeatTradeFact:
    event_id: str
    seat_raw: str
    buy_fen: int
    sell_fen: int
    net_fen: int
    available_at: str
    source: str
    revision: int = 1
    seat_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.seat_raw:
            raise LhbContractError("席位金额事实缺少 event_id/seat_raw")
        if self.revision < 1:
            raise LhbContractError(f"非法 revision: {self.revision}")
        if not self.source.strip():
            raise LhbContractError("缺少 source")
        validate_seat_amounts(
            buy_fen=self.buy_fen, sell_fen=self.sell_fen, net_fen=self.net_fen
        )
        object.__setattr__(self, "available_at", require_available_at(self.available_at))


@dataclass(frozen=True)
class LhbSeatRankFact:
    event_id: str
    seat_raw: str
    side: str
    rank_no: int
    available_at: str
    source: str
    revision: int = 1
    seat_id: str | None = None

    def __post_init__(self) -> None:
        parse_enum(self.side, RANK_SIDE_VALUES, label="side")
        if self.rank_no < 1:
            raise LhbContractError(f"非法排名: {self.rank_no}")
        if not self.event_id or not self.seat_raw:
            raise LhbContractError("席位排名缺少 event_id/seat_raw")
        object.__setattr__(self, "available_at", require_available_at(self.available_at))


@dataclass(frozen=True)
class LhbSeatLegs:
    trade: LhbSeatTradeFact
    ranks: tuple[LhbSeatRankFact, ...]


def materialize_seat_legs(
    *,
    event_id: str,
    seat_raw: str,
    buy_amount: Any,
    sell_amount: Any,
    unit: AmountUnit | str,
    buy_rank: int | None,
    sell_rank: int | None,
    available_at: str,
    source: str,
    net_amount: Any | None = None,
) -> LhbSeatLegs:
    """同席位可同时进买榜和卖榜：金额只生成一条事实，排名各留一行。"""
    buy_fen = to_fen(buy_amount, unit)
    sell_fen = to_fen(sell_amount, unit)
    net_fen = buy_fen - sell_fen if net_amount is None else to_fen(net_amount, unit)
    trade = LhbSeatTradeFact(
        event_id=event_id,
        seat_raw=seat_raw,
        buy_fen=buy_fen,
        sell_fen=sell_fen,
        net_fen=net_fen,
        available_at=available_at,
        source=source,
    )
    ranks: list[LhbSeatRankFact] = []
    if buy_rank is not None:
        ranks.append(
            LhbSeatRankFact(
                event_id=event_id,
                seat_raw=seat_raw,
                side="BUY",
                rank_no=buy_rank,
                available_at=available_at,
                source=source,
            )
        )
    if sell_rank is not None:
        ranks.append(
            LhbSeatRankFact(
                event_id=event_id,
                seat_raw=seat_raw,
                side="SELL",
                rank_no=sell_rank,
                available_at=available_at,
                source=source,
            )
        )
    if not ranks:
        raise LhbContractError("席位未进入买榜或卖榜")
    return LhbSeatLegs(trade=trade, ranks=tuple(ranks))


def content_hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]
