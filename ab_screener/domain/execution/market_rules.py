"""v2 市场规则：可交易性判定 + tick + 滑点钳制 + 涨跌停。"""
from __future__ import annotations

from ab_screener.domain.execution.models import (
    TICK_MICRO,
    MoneyError,
    Quote,
    Side,
)

DEFAULT_LIMIT_RATIO_BPS = 1000  # ±10%


def limit_prices_micro(
    quote: Quote, limit_ratio_bps: int = DEFAULT_LIMIT_RATIO_BPS
) -> tuple[int, int]:
    """(涨停价, 跌停价) 微元，按 tick 四舍五入。"""
    if not quote.pre_close_micro or quote.pre_close_micro <= 0:
        return (0, 0)
    pre = quote.pre_close_micro
    up = _round_half_up(pre * (10_000 + limit_ratio_bps), 10_000)
    down = _round_half_up(pre * (10_000 - limit_ratio_bps), 10_000)
    return tick_round_micro(up), tick_round_micro(down)


def tick_round_micro(price_micro: int, tick_micro: int = TICK_MICRO) -> int:
    """价格按最小变动单位取整（A 股 0.01 元；round-half-up）。"""
    if price_micro <= 0:
        return 0
    return (int(price_micro) + tick_micro // 2) // tick_micro * tick_micro


def _round_half_up(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def can_trade(quote: Quote, side: Side) -> tuple[bool, str]:
    """零成交判定：无报价/无量/一字涨停买/一字跌停卖。"""
    if quote.open_micro <= 0 or quote.close_micro <= 0:
        return False, "NO_QUOTE"
    if quote.vol <= 0:
        return False, "NO_VOLUME"
    up, down = limit_prices_micro(quote)
    if (
        side == "BUY"
        and up > 0
        and quote.open_micro == up
        and quote.high_micro == up
        and quote.low_micro == up
    ):
        # 一字涨停：开=高=低=涨停且无法买入
        return False, "LIMIT_UP_ONE_SIDE"
    if (
        side == "SELL"
        and down > 0
        and quote.open_micro == down
        and quote.high_micro == down
        and quote.low_micro == down
    ):
        return False, "LIMIT_DOWN_ONE_SIDE"
    return True, ""


def slipped_price_micro(
    price_micro: int,
    side: Side,
    quote: Quote,
    slippage_bps: int,
) -> int:
    """滑点后价格：买向上、卖向下，再钳制在当日 [low, high] 并按 tick 取整。"""
    if price_micro <= 0:
        raise MoneyError("滑点输入价格必须为正")
    if side == "BUY":
        moved = _round_half_up(price_micro * (10_000 + slippage_bps), 10_000)
    else:
        moved = _round_half_up(price_micro * (10_000 - slippage_bps), 10_000)
    if quote.high_micro > 0 and quote.low_micro > 0 and quote.high_micro >= quote.low_micro:
        moved = max(min(moved, quote.high_micro), quote.low_micro)
    return tick_round_micro(moved)


def participation_max_qty(vol: int, participation_bps: int) -> int:
    """按版本化参与率（默认 5%）计算最大可成交股数（向下取整）。"""
    if vol < 0 or participation_bps < 0 or participation_bps > 10_000:
        raise MoneyError("参与率或成交量非法")
    return int(vol) * participation_bps // 10_000


def floor_to_lot(qty: int, lot_size: int = 100) -> int:
    """按交易单位向下取整。"""
    if qty <= 0:
        return 0
    return int(qty) // lot_size * lot_size
