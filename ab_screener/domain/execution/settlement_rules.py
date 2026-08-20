"""v2 结算规则：现金预算整手、持仓约束、FIFO 批次消耗、T+1 可卖日。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ab_screener.domain.execution.models import MoneyError


def available_buy_qty_by_cash(cash_fen: int, price_micro: int, lot_size: int = 100) -> int:
    """现金预算内（仅按价格粗算）可买整手；精确含费校验在 fill_model 内做。"""
    if cash_fen < 0:
        raise MoneyError("现金不能为负")
    if price_micro <= 0:
        return 0
    # 1 分 = 10000 微元
    return int(cash_fen) * 10_000 // int(price_micro) // lot_size * lot_size


def available_sell_qty(position_qty: int, lot_size: int = 100) -> int:
    """可卖数量 = 持仓向下取整到整手；持仓为负 → 拒绝（超卖）。"""
    if position_qty < 0:
        raise MoneyError("持仓不能为负（超卖拒绝）")
    return int(position_qty) // lot_size * lot_size


@dataclass(frozen=True)
class Lot:
    """一个买入批次（FIFO 消耗的最小单元）。"""

    lot_id: Any
    ts_code: str
    qty: int
    cost_price_micro: int
    sellable_date: str

    def __post_init__(self) -> None:
        if self.qty < 0:
            raise MoneyError("批次数量不能为负")
        if not self.cost_price_micro or self.cost_price_micro <= 0:
            raise MoneyError("批次成本价必须为正（整数微元）")


@dataclass(frozen=True)
class FifoResult:
    consumed: list[tuple[Any, int]]   # [(lot_id, consumed_qty)]
    realized_pnl_fen: int
    remainder_qty: int                # 超出持仓的请求量（应为 0，超卖则抛错）


def consume_fifo_lots(
    lots: list[Lot],
    qty: int,
    sell_price_micro: int,
) -> FifoResult:
    """FIFO 消耗批次；卖出量 > 可用持仓 → 超卖拒绝。返回已实现盈亏（分）。"""
    if qty <= 0:
        raise MoneyError("卖出数量必须为正")
    total_available = sum(l.qty for l in lots)
    if qty > total_available:
        raise MoneyError(
            f"超卖拒绝: 请求 {qty} > 可用持仓 {total_available}"
        )
    remaining = qty
    consumed: list[tuple[Any, int]] = []
    pnl_fen = 0
    for lot in lots:
        if remaining <= 0:
            break
        take = min(lot.qty, remaining)
        consumed.append((lot.lot_id, take))
        cost_fen = int(lot.cost_price_micro) * take // 10_000   # 1 分 = 10000 微元
        sell_fen = int(sell_price_micro) * take // 10_000
        pnl_fen += sell_fen - cost_fen
        remaining -= take
    return FifoResult(
        consumed=consumed,
        realized_pnl_fen=pnl_fen,
        remainder_qty=remaining,
    )


def next_sellable_date(trade_date: str, open_dates: list[str]) -> str | None:
    """T+1：下一个开市日；无 → None（fail-closed）。"""
    future = [d for d in open_dates if d > trade_date]
    return future[0] if future else None
