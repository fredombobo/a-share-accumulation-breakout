"""研究读取层的可用时点口径（ADR-022 决策 1，版本 rule-v1）。

每条输入要么是 CAPTURED（库中 available_at 为真实抓取时刻），要么按下列冻结规则
RULE_DERIVED；两者在报告中分开统计。本模块只计算与标注，不回写数据库。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")

POLICY_VERSION = "rule-v1"
Basis = Literal["CAPTURED", "RULE_DERIVED"]

# 数据集 → (推定时刻, 相对日期的天数偏移)；None 表示只接受 CAPTURED
_RULES: dict[str, tuple[time, int] | None] = {
    "daily": (time(18, 0), 0),
    "daily_basic": (time(18, 0), 0),
    "moneyflow": (time(18, 0), 0),
    "adj_factor": (time(18, 0), 0),
    "index_daily": (time(18, 0), 0),
    "top_list": (time(20, 0), 0),
    "top_inst": (time(20, 0), 0),
    "fina_indicator": (time(23, 59), 0),
    "income": (time(23, 59), 0),
    "balancesheet": (time(23, 59), 0),
    "cashflow": (time(23, 59), 0),
    "stock_basic": (time(0, 0), 0),
    "delisted_basic": (time(0, 0), 0),
    "cyq": None,
}
_FINANCIAL = frozenset({"fina_indicator", "income", "balancesheet", "cashflow"})
DECISION_TIME = time(9, 15)


class AvailabilityPolicyError(ValueError):
    """口径不允许推定，或缺少推定所需的日期字段。"""


def _day(value: str) -> datetime:
    text = str(value).replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise AvailabilityPolicyError(f"日期必须是 YYYYMMDD: {value!r}")
    return datetime.strptime(text, "%Y%m%d").replace(tzinfo=_TZ)


def rule_available_at(
    dataset: str,
    *,
    trade_date: str | None = None,
    f_ann_date: str | None = None,
    ann_date: str | None = None,
    first_disclosure: bool | None = None,
) -> datetime:
    """按 rule-v1 推定可用时刻；不允许推定的数据集或缺字段一律拒绝。"""
    if dataset not in _RULES:
        raise AvailabilityPolicyError(f"rule-v1 未定义数据集 {dataset}；新增须修订 ADR-022")
    rule = _RULES[dataset]
    if rule is None:
        raise AvailabilityPolicyError(f"{dataset} 只接受真实捕获时点（CAPTURED），不得推定")
    at, offset = rule
    if dataset in _FINANCIAL:
        if first_disclosure is not True:
            raise AvailabilityPolicyError("财务数据只允许首次披露版本；修订值需版本链，否则 BLOCKED")
        source = f_ann_date or ann_date
        if not source:
            raise AvailabilityPolicyError("财务数据缺少 f_ann_date / ann_date")
        base = _day(source)
    else:
        if not trade_date:
            raise AvailabilityPolicyError(f"{dataset} 推定需要 trade_date")
        base = _day(trade_date)
    return (base + timedelta(days=offset)).replace(hour=at.hour, minute=at.minute)


def decision_at(next_open_day: str) -> datetime:
    """决策时点：次一交易日 09:15（调用方负责按交易日历给出次一交易日）。"""
    return _day(next_open_day).replace(hour=DECISION_TIME.hour, minute=DECISION_TIME.minute)


def usable(available_at: datetime, decision: datetime) -> bool:
    return available_at <= decision


def classify(dataset: str, captured_available_at: datetime | None) -> Basis:
    """有真实捕获时刻即 CAPTURED；否则须能按规则推定，才是 RULE_DERIVED。"""
    if captured_available_at is not None:
        return "CAPTURED"
    if _RULES.get(dataset, None) is None:
        raise AvailabilityPolicyError(f"{dataset} 无捕获时点且不允许推定")
    return "RULE_DERIVED"
