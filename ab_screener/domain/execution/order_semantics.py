"""订单语义（P2.2）：DAY 余量过期、时间护栏、停牌不算实际撮合。

规则（P2.2）：
- DAY 订单：当日实际撮合后的余量在收盘过期（不自动顺延）。
- 停牌/无量/无报价不算实际撮合（零成交，订单保留语义由调用方决定）。
- 时间护栏：同日收盘信号 → 同一收盘成交的任何路径必须失败（防偷看）。
"""
from __future__ import annotations

from dataclasses import dataclass

from ab_screener.domain.execution.models import MoneyError, Quote


def expire_day_remainder(requested_qty: int, filled_qty: int) -> int:
    """DAY 订单余量 = 请求 - 实际成交；<=0 → 无余量（全部成交）。"""
    if requested_qty < 0 or filled_qty < 0:
        raise MoneyError("数量不能为负")
    if filled_qty > requested_qty:
        raise MoneyError(f"成交 {filled_qty} 超过请求 {requested_qty}（重复成交拒绝）")
    return requested_qty - filled_qty


def suspension_is_not_fill(quote: Quote) -> bool:
    """停牌/无量/无报价 → 不算实际撮合。"""
    return quote.open_micro <= 0 or quote.close_micro <= 0 or quote.vol <= 0


@dataclass(frozen=True)
class SignalTiming:
    signal_date: str
    signal_close_micro: int
    fill_date: str
    fill_time: str  # "OPEN" | "CLOSE"


def assert_no_same_close_fill(timing: SignalTiming) -> None:
    """收盘信号 → 当日收盘成交拒绝；信号次日开盘才可成交（ENTRY V1）。"""
    if timing.signal_date == timing.fill_date and timing.fill_time == "CLOSE":
        raise MoneyError(
            f"时间护栏: {timing.signal_date} 收盘信号按同一收盘成交（禁止偷看）"
        )
