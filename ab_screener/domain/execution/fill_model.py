"""v2 撮合模型：参与率限量、资金/持仓约束、费用拆解、拒绝负现金/超卖。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ab_screener.domain.execution.fees import FeeParams, compute_fees
from ab_screener.domain.execution.market_rules import (
    can_trade,
    floor_to_lot,
    participation_max_qty,
    slipped_price_micro,
)
from ab_screener.domain.execution.models import (
    FeeBreakdown,
    FillV2,
    MoneyError,
    Quote,
    Side,
    require_int_fen,
)
from ab_screener.domain.execution.settlement_rules import (
    available_buy_qty_by_cash,
    available_sell_qty,
)


@dataclass(frozen=True)
class FillRequest:
    ts_code: str
    side: Side
    trade_date: str
    input_hash: str
    participation_bps: int = 500  # 默认 5%
    lot_size: int = 100
    cash_available_fen: int | None = None  # 买入约束（None=不检查）
    position_qty: int | None = None  # 卖出约束（None=不检查）
    requested_qty: int | None = None  # 订单请求数量（None=按参与率上限全额撮合）
    fees: FeeParams = field(default_factory=FeeParams)

    def __post_init__(self) -> None:
        if not self.input_hash:
            raise MoneyError("撮合请求必须携带 input_hash（防重复成交）")
        if self.cash_available_fen is not None:
            require_int_fen(self.cash_available_fen, name="cash_available_fen")
        if self.requested_qty is not None and (
            not isinstance(self.requested_qty, int) or self.requested_qty < 0
        ):
            raise MoneyError(f"requested_qty 必须为非负整数: {self.requested_qty!r}")
        if self.participation_bps < 0 or self.participation_bps > 10_000:
            raise MoneyError(f"参与率非法: {self.participation_bps} bps")


def compute_fill(quote: Quote, request: FillRequest) -> FillV2:
    """确定性撮合：返回 v2 成交（含费用拆解与现金变动）。"""
    ok, reason = can_trade(quote, request.side)
    zero = lambda why: FillV2(
        ts_code=request.ts_code,
        side=request.side,
        trade_date=request.trade_date,
        filled=False,
        qty=0,
        price_micro=0,
        notional_fen=0,
        fees=compute_fees(0, request.side, request.fees, slippage_notional_fen=0),
        cash_delta_fen=0,
        reason=why,
        participation_bps=request.participation_bps,
        max_qty=0,
        input_hash=request.input_hash,
    )
    if not ok:
        return zero(reason)

    ref_micro = quote.open_micro
    px_micro = slipped_price_micro(ref_micro, request.side, quote, request.fees.slippage_bps)
    max_qty = participation_max_qty(quote.vol, request.participation_bps)
    max_qty = floor_to_lot(max_qty, request.lot_size)
    if request.requested_qty is not None:
        requested = floor_to_lot(request.requested_qty, request.lot_size)
        if requested <= 0:
            return zero("INVALID_QTY")
        max_qty = min(max_qty, requested)
    liquidity_exhausted = max_qty <= 0

    if request.side == "BUY":
        if request.cash_available_fen is not None:
            max_by_cash = available_buy_qty_by_cash(request.cash_available_fen, px_micro, request.lot_size)
            max_qty = min(max_qty, max_by_cash)
        qty = max_qty
        if qty <= 0:
            if liquidity_exhausted:
                return zero("INSUFFICIENT_LIQUIDITY")
            return zero("INSUFFICIENT_CASH")
        notional_fen = _notional_fen(px_micro, qty)
        fees = _fill_fees(notional_fen, "BUY", request.fees, ref_micro, px_micro, qty)
        total_debit = notional_fen + fees.commission_fen + fees.stamp_tax_fen + fees.other_fee_fen
        if request.cash_available_fen is not None and total_debit > request.cash_available_fen:
            # 现金不足则降档到整手（保守）；仍不足 → 零成交
            qty = _largest_lot_within(px_micro, request.cash_available_fen, request.lot_size, request.fees)
            if qty <= 0:
                return zero("INSUFFICIENT_CASH")
            notional_fen = _notional_fen(px_micro, qty)
            fees = _fill_fees(notional_fen, "BUY", request.fees, ref_micro, px_micro, qty)
            total_debit = notional_fen + fees.commission_fen + fees.stamp_tax_fen + fees.other_fee_fen
            if total_debit > request.cash_available_fen:
                return zero("INSUFFICIENT_CASH")
        cash_delta = -total_debit
    else:
        if request.position_qty is not None:
            max_qty = min(max_qty, available_sell_qty(request.position_qty, request.lot_size))
        qty = max_qty
        if qty <= 0:
            if liquidity_exhausted:
                return zero("INSUFFICIENT_LIQUIDITY")
            return zero("NO_POSITION")
        notional_fen = _notional_fen(px_micro, qty)
        fees = _fill_fees(notional_fen, "SELL", request.fees, ref_micro, px_micro, qty)
        cash_delta = notional_fen - fees.commission_fen - fees.stamp_tax_fen - fees.other_fee_fen

    return FillV2(
        ts_code=request.ts_code,
        side=request.side,
        trade_date=request.trade_date,
        filled=True,
        qty=qty,
        price_micro=px_micro,
        notional_fen=notional_fen,
        fees=fees,
        cash_delta_fen=cash_delta,
        reason="ok",
        participation_bps=request.participation_bps,
        max_qty=max_qty,
        input_hash=request.input_hash,
    )


def _notional_fen(price_micro: int, qty: int) -> int:
    """名义金额（分）：1 分 = 10000 微元。"""
    return int(price_micro) * int(qty) // 10_000


def _fill_fees(
    notional_fen: int,
    side: Side,
    params: FeeParams,
    reference_price_micro: int,
    fill_price_micro: int,
    qty: int,
) -> FeeBreakdown:
    """Report the actual tick/clamp-adjusted slippage already embedded in price."""
    base = compute_fees(notional_fen, side, params, slippage_notional_fen=0)
    return FeeBreakdown(
        commission_fen=base.commission_fen,
        stamp_tax_fen=base.stamp_tax_fen,
        other_fee_fen=base.other_fee_fen,
        slippage_fen=_notional_fen(abs(fill_price_micro - reference_price_micro), qty),
    )


def _largest_lot_within(price_micro: int, cash_fen: int, lot_size: int, fees: FeeParams) -> int:
    """在现金预算内（含佣金/其他费）可买的整手数（保守逐档搜索）。"""
    if price_micro <= 0:
        return 0
    rough = cash_fen * 10_000 // price_micro // lot_size
    for lots in range(rough, -1, -1):
        qty = lots * lot_size
        notional = _notional_fen(price_micro, qty)
        fees_total = (
            compute_fees(notional, "BUY", fees, slippage_notional_fen=0).total_fen()
            - compute_fees(notional, "BUY", fees, slippage_notional_fen=0).slippage_fen
        )
        if notional + fees_total <= cash_fen:
            return qty
    return 0
