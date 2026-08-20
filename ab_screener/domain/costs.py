"""固定口径成交与成本引擎（upgrade system §2）。

- 名义资金 100_000 元/笔，数量 100 股整手下取整
- 收盘信号 → 下一可交易日开盘尝试成交
- 成本口径统一 config（2026-08-16 起研究/纸面共用）：佣金万五、每边最低 5 元；
  卖出印花税千一；双边其他费万一；双边滑点万十
- 停牌/无量/一字涨停买/一字跌停卖 → 零成交
- 滑点后价格夹在当日高低价
- 同日止损与目标同时触发 → 止损优先
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import (
    COMMISSION_MIN_YUAN,
    COMMISSION_RATE,
    OTHER_FEE_RATE,
    SLIPPAGE_RATE,
    STAMP_TAX_SELL,
)

NOTIONAL = 100_000.0
LOT = 100
COMMISSION_MIN = COMMISSION_MIN_YUAN
SLIPPAGE = SLIPPAGE_RATE

# v2 执行核心（P2.1）版本标记：旧核心保持冻结，切换写路径必须经过 dual-run。
LEGACY_FILL_MODEL_VERSION = "legacy-v1"
V2_FILL_MODEL_VERSION = "v2.1.0"


def dual_run_observer() -> dict:
    """v2 写路径切换门：返回当前是否允许 v2 接管。

    契约（P2.1）：先 observe/dual-run 对比旧核心；不一致时不切换写路径。
    当前 v2 与旧核心在「标准买卖/费用原语」上已对齐（tests/test_execution_*），
    但纸面/研究写路径的切换需待 P2.2 可成交语义与 parity 测试完成。
    """
    return {
        "v2_ready_for_write_path": False,
        "reason": "P2.2 可成交语义/parity 测试未完成前不切换写路径",
        "legacy_version": LEGACY_FILL_MODEL_VERSION,
        "v2_version": V2_FILL_MODEL_VERSION,
    }

# 默认成本快照：工作台自定义成本在 worker 内临时覆盖后，按此恢复
COST_KEYS_DEFAULT = {
    "commission_rate": float(COMMISSION_RATE),
    "commission_min": float(COMMISSION_MIN),
    "stamp_tax_sell": float(STAMP_TAX_SELL),
    "other_fee_rate": float(OTHER_FEE_RATE),
    "slippage": float(SLIPPAGE),
}


@dataclass
class FillResult:
    filled: bool
    qty: int
    price: float
    commission: float
    stamp_tax: float
    other_fee: float
    slippage_cost: float
    gross_pnl: float
    net_pnl: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "filled": self.filled,
            "qty": self.qty,
            "price": self.price,
            "commission": round(self.commission, 4),
            "stamp_tax": round(self.stamp_tax, 4),
            "other_fee": round(self.other_fee, 4),
            "slippage_cost": round(self.slippage_cost, 4),
            "gross_pnl": round(self.gross_pnl, 4),
            "net_pnl": round(self.net_pnl, 4),
            "reason": self.reason,
        }


def _round_lot(qty: float) -> int:
    return int(qty // LOT) * LOT


def commission_for(notional: float) -> float:
    return max(COMMISSION_MIN, abs(notional) * COMMISSION_RATE)


def other_fee_for(notional: float) -> float:
    return abs(notional) * OTHER_FEE_RATE


def stamp_for_sell(notional: float) -> float:
    return abs(notional) * STAMP_TAX_SELL


def apply_slippage(price: float, *, side: str, high: float, low: float) -> float:
    """side: buy|sell。滑点后夹在 [low, high]。"""
    if side == "buy":
        p = price * (1.0 + SLIPPAGE)
    else:
        p = price * (1.0 - SLIPPAGE)
    if high > 0 and low > 0 and high >= low:
        p = min(max(p, low), high)
    return float(p)


def can_buy(
    *,
    open_p: float | None,
    high: float | None,
    low: float | None,
    vol: float | None,
    pre_close: float | None,
    limit_up_ratio: float = 0.10,
) -> tuple[bool, str]:
    if open_p is None or open_p <= 0 or vol is None or vol <= 0:
        return False, "停牌或无量"
    if pre_close and pre_close > 0 and high is not None and low is not None:
        limit_up = round(pre_close * (1.0 + limit_up_ratio), 2)
        # 一字涨停：开高低收贴近涨停且无法买入
        if abs(open_p - limit_up) < 1e-6 and abs((high or 0) - limit_up) < 1e-6 and abs((low or 0) - limit_up) < 1e-6:
            return False, "一字涨停无法买入"
    return True, ""


def can_sell(
    *,
    open_p: float | None,
    high: float | None,
    low: float | None,
    vol: float | None,
    pre_close: float | None,
    limit_down_ratio: float = 0.10,
) -> tuple[bool, str]:
    if open_p is None or open_p <= 0 or vol is None or vol <= 0:
        return False, "停牌或无量"
    if pre_close and pre_close > 0 and high is not None and low is not None:
        limit_dn = round(pre_close * (1.0 - limit_down_ratio), 2)
        if abs(open_p - limit_dn) < 1e-6 and abs((high or 0) - limit_dn) < 1e-6 and abs((low or 0) - limit_dn) < 1e-6:
            return False, "一字跌停无法卖出"
    return True, ""


def size_buy(entry_price: float, notional: float = NOTIONAL) -> int:
    if entry_price <= 0:
        return 0
    return _round_lot(notional / entry_price)


def simulate_round_trip(
    *,
    entry_open: float,
    entry_high: float,
    entry_low: float,
    entry_vol: float,
    entry_pre_close: float | None,
    exit_open: float,
    exit_high: float,
    exit_low: float,
    exit_vol: float,
    exit_pre_close: float | None,
    stop_price: float | None = None,
    target_price: float | None = None,
    exit_day_low: float | None = None,
    exit_day_high: float | None = None,
    notional: float = NOTIONAL,
) -> FillResult:
    """简化单笔往返：次日开买入、某日开卖出；若当日触及止损/目标用保守序。"""
    ok, reason = can_buy(
        open_p=entry_open, high=entry_high, low=entry_low, vol=entry_vol, pre_close=entry_pre_close
    )
    if not ok:
        return FillResult(False, 0, 0.0, 0, 0, 0, 0, 0, 0, reason)

    buy_px = apply_slippage(entry_open, side="buy", high=entry_high, low=entry_low)
    qty = size_buy(buy_px, notional)
    if qty <= 0:
        return FillResult(False, 0, 0.0, 0, 0, 0, 0, 0, 0, "整手不足")

    buy_notional = buy_px * qty
    buy_comm = commission_for(buy_notional)
    buy_other = other_fee_for(buy_notional)
    buy_slip = abs(buy_px - entry_open) * qty

    # 卖出：若同日止损与目标都触发，止损优先
    sell_side_ok, sell_reason = can_sell(
        open_p=exit_open, high=exit_high, low=exit_low, vol=exit_vol, pre_close=exit_pre_close
    )
    if not sell_side_ok:
        return FillResult(
            False, qty, buy_px, buy_comm, 0, buy_other, buy_slip, 0, -(buy_comm + buy_other), sell_reason
        )

    hi = exit_day_high if exit_day_high is not None else exit_high
    lo = exit_day_low if exit_day_low is not None else exit_low
    exit_px = exit_open
    if stop_price is not None and lo is not None and lo <= stop_price:
        exit_px = stop_price
    elif target_price is not None and hi is not None and hi >= target_price:
        exit_px = target_price

    sell_px = apply_slippage(exit_px, side="sell", high=exit_high, low=exit_low)
    sell_notional = sell_px * qty
    sell_comm = commission_for(sell_notional)
    sell_other = other_fee_for(sell_notional)
    stamp = stamp_for_sell(sell_notional)
    sell_slip = abs(exit_px - sell_px) * qty

    gross = (sell_px - buy_px) * qty
    costs = buy_comm + sell_comm + buy_other + sell_other + stamp
    slip_cost = buy_slip + sell_slip
    net = gross - costs  # 滑点已进成交价，slip_cost 单独报告

    return FillResult(
        filled=True,
        qty=qty,
        price=sell_px,
        commission=buy_comm + sell_comm,
        stamp_tax=stamp,
        other_fee=buy_other + sell_other,
        slippage_cost=slip_cost,
        gross_pnl=gross,
        net_pnl=net,
        reason="ok",
    )


def summarize_fills(fills: list[FillResult]) -> dict[str, Any]:
    filled = [f for f in fills if f.filled]
    n = len(filled)
    if n == 0:
        return {
            "n_trades": 0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "commission": 0.0,
            "stamp_tax": 0.0,
            "other_fee": 0.0,
            "slippage_cost": 0.0,
            "unfilled": len(fills) - n,
            "net_avg_return": None,
            "net_win_rate": None,
            "net_profit_factor": None,
            "net_max_drawdown": None,
        }
    returns = [f.net_pnl / NOTIONAL for f in filled]
    positives = [value for value in returns if value > 0]
    negatives = [value for value in returns if value < 0]
    profit_factor = (
        sum(positives) / abs(sum(negatives)) if positives and negatives else None
    )
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, 1.0 - equity / peak)
    return {
        "n_trades": n,
        "gross_pnl": round(sum(f.gross_pnl for f in filled), 4),
        "net_pnl": round(sum(f.net_pnl for f in filled), 4),
        "commission": round(sum(f.commission for f in filled), 4),
        "stamp_tax": round(sum(f.stamp_tax for f in filled), 4),
        "other_fee": round(sum(f.other_fee for f in filled), 4),
        "slippage_cost": round(sum(f.slippage_cost for f in filled), 4),
        "unfilled": len(fills) - n,
        "net_avg_return": round(sum(returns) / n, 6),
        "net_win_rate": round(len(positives) / n, 4),
        "net_profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "net_max_drawdown": round(max_drawdown, 4),
    }
