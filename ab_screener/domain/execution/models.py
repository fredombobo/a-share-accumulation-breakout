"""v2 执行域模型：金额整数分、价格整数微元、拒绝浮点账务输入。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EXECUTION_MODEL_VERSION = "v2.1.0"
FEE_VERSION = "v2-fixed-2026-08-18"
TICK_MICRO = 10_000  # A 股最小变动 0.01 元 = 10000 微元

Side = Literal["BUY", "SELL"]


class MoneyError(ValueError):
    """金额/数量非法：浮点输入、负现金、超卖等统一拒绝。"""


def require_int_fen(value: Any, *, name: str) -> int:
    """金额必须是整数分；float（含 bool）输入直接拒绝（账务精度 fail-closed）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyError(f"{name} 必须是整数分（拒绝浮点账务输入）: {value!r}")
    return value


def require_int_micro(value: Any, *, name: str) -> int:
    """价格必须是整数微元。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyError(f"{name} 必须是整数微元: {value!r}")
    return value


@dataclass(frozen=True)
class Quote:
    """一个交易日的一根 K 线（价格微元，数量股）。"""

    ts_code: str
    trade_date: str
    open_micro: int
    high_micro: int
    low_micro: int
    close_micro: int
    vol: int          # 股
    amount_fen: int   # 成交额（分）
    pre_close_micro: int | None = None
    available_at: str = ""  # +08:00（PIT）

    def __post_init__(self) -> None:
        for name, value in (
            ("open_micro", self.open_micro), ("high_micro", self.high_micro),
            ("low_micro", self.low_micro), ("close_micro", self.close_micro),
        ):
            require_int_micro(value, name=name)
        require_int_fen(self.amount_fen, name="amount_fen")
        if not isinstance(self.vol, int) or self.vol < 0:
            raise MoneyError(f"vol 必须为非负整数: {self.vol!r}")


@dataclass(frozen=True)
class FeeBreakdown:
    commission_fen: int
    stamp_tax_fen: int
    other_fee_fen: int
    slippage_fen: int

    def total_fen(self) -> int:
        return self.commission_fen + self.stamp_tax_fen + self.other_fee_fen + self.slippage_fen

    def to_dict(self) -> dict[str, int]:
        return {
            "commission_fen": self.commission_fen,
            "stamp_tax_fen": self.stamp_tax_fen,
            "other_fee_fen": self.other_fee_fen,
            "slippage_fen": self.slippage_fen,
        }


@dataclass(frozen=True)
class FillV2:
    """v2 成交结果：数量/价格/费用拆解/参与率/模型版本/输入哈希。"""

    ts_code: str
    side: Side
    trade_date: str
    filled: bool
    qty: int
    price_micro: int
    notional_fen: int
    fees: FeeBreakdown
    cash_delta_fen: int
    reason: str
    participation_bps: int
    max_qty: int
    input_hash: str
    model_version: str = EXECUTION_MODEL_VERSION
    fee_version: str = FEE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "side": self.side,
            "trade_date": self.trade_date,
            "filled": self.filled,
            "qty": self.qty,
            "price_micro": self.price_micro,
            "notional_fen": self.notional_fen,
            "fee_breakdown": self.fees.to_dict(),
            "cash_delta_fen": self.cash_delta_fen,
            "reason": self.reason,
            "participation_bps": self.participation_bps,
            "max_qty": self.max_qty,
            "input_hash": self.input_hash,
            "model_version": self.model_version,
            "fee_version": self.fee_version,
        }
