"""唯一执行领域核心（P2.1）：撮合/费用/交易规则/账本精度的 v2 域模型。

契约：
- 金额一律整数「分」（fen），价格用整数微元（micro = 元 × 1_000_000）；
  浮点账务输入直接拒绝（fail-closed）。
- 所有规则可逐项复算（tick/滑点/佣金最低值/税费/FIFO/T+1），误差为零分。
- 未知费用版本拒绝；负现金、超卖、重复成交拒绝。
- v2 与旧核心（ab_screener.domain.costs / trade_sim）先 dual-run 对比，
  不一致时不切换写路径（见 execution.dual_run）。
"""
from __future__ import annotations

from ab_screener.domain.execution.fees import fee_version
from ab_screener.domain.execution.fill_model import compute_fill
from ab_screener.domain.execution.market_rules import can_trade
from ab_screener.domain.execution.models import (
    EXECUTION_MODEL_VERSION,
    FillV2,
    MoneyError,
    Quote,
    require_int_fen,
)
from ab_screener.domain.execution.order_semantics import (
    SignalTiming,
    assert_no_same_close_fill,
    expire_day_remainder,
    suspension_is_not_fill,
)
from ab_screener.domain.execution.settlement_rules import consume_fifo_lots

__all__ = [
    "EXECUTION_MODEL_VERSION",
    "FillV2",
    "MoneyError",
    "Quote",
    "SignalTiming",
    "assert_no_same_close_fill",
    "can_trade",
    "compute_fill",
    "consume_fifo_lots",
    "expire_day_remainder",
    "fee_version",
    "require_int_fen",
    "suspension_is_not_fill",
]
