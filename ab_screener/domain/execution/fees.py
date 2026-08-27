"""v2 费用引擎：整数分精确计算（佣金最低值/印花税/其他费/滑点）。"""

from __future__ import annotations

from dataclasses import dataclass

from ab_screener.domain.execution.models import FEE_VERSION, FeeBreakdown, MoneyError, Side

# 默认参数（与 config/paper 口径一致：万五佣金/最低5元/卖出印花千一/其他万一/滑点万十）
DEFAULT_COMMISSION_BPS = 5
DEFAULT_COMMISSION_MIN_FEN = 500
DEFAULT_STAMP_TAX_BPS = 10
DEFAULT_OTHER_FEE_BPS = 1
DEFAULT_SLIPPAGE_BPS = 10


def fee_version() -> str:
    return FEE_VERSION


@dataclass(frozen=True)
class FeeParams:
    commission_bps: int = DEFAULT_COMMISSION_BPS
    commission_min_fen: int = DEFAULT_COMMISSION_MIN_FEN
    stamp_tax_bps: int = DEFAULT_STAMP_TAX_BPS
    other_fee_bps: int = DEFAULT_OTHER_FEE_BPS
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS
    version: str = FEE_VERSION

    def __post_init__(self) -> None:
        if self.version != FEE_VERSION:
            raise MoneyError(f"未知费用版本: {self.version}（当前 {FEE_VERSION}）")


def _round_half_up(numerator: int, denominator: int) -> int:
    """整数四舍五入到最近分（round-half-up，确定性）。"""
    return (numerator + denominator // 2) // denominator


def commission_fen(notional_fen: int, params: FeeParams) -> int:
    """佣金 = max(最低, 名义额 × 费率)，整数分。"""
    if notional_fen < 0:
        raise MoneyError("名义金额不能为负")
    raw = _round_half_up(notional_fen * params.commission_bps, 10_000)
    return max(params.commission_min_fen, raw)


def stamp_tax_fen(notional_fen: int, params: FeeParams) -> int:
    """卖出印花税（买入为 0）。"""
    if notional_fen < 0:
        raise MoneyError("名义金额不能为负")
    return _round_half_up(notional_fen * params.stamp_tax_bps, 10_000)


def other_fee_fen(notional_fen: int, params: FeeParams) -> int:
    if notional_fen < 0:
        raise MoneyError("名义金额不能为负")
    return _round_half_up(notional_fen * params.other_fee_bps, 10_000)


def slippage_fen(notional_fen: int, params: FeeParams) -> int:
    """滑点成本 = 名义额 × 滑点率（滑点已体现在成交价中，此项为拆解报告）。"""
    if notional_fen < 0:
        raise MoneyError("名义金额不能为负")
    return _round_half_up(notional_fen * params.slippage_bps, 10_000)


def compute_fees(
    notional_fen: int,
    side: Side,
    params: FeeParams,
    *,
    slippage_notional_fen: int,
) -> FeeBreakdown:
    """费用拆解；slippage_notional_fen 是用于按费率估算滑点的参考名义金额。"""
    commission = commission_fen(notional_fen, params)
    stamp = stamp_tax_fen(notional_fen, params) if side == "SELL" else 0
    other = other_fee_fen(notional_fen, params)
    slippage = slippage_fen(slippage_notional_fen, params)
    return FeeBreakdown(
        commission_fen=commission,
        stamp_tax_fen=stamp,
        other_fee_fen=other,
        slippage_fen=slippage,
    )
