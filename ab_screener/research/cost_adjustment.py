"""把候选策略的旧回放结果按统一的真实成本口径重新定价。"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ab_screener.domain.costs import NOTIONAL, simulate_round_trip


def _number(row: pd.Series, name: str, default: float = 0.0) -> float:
    value = row.get(name, default)
    if value is None or pd.isna(value):
        return default
    return float(value)


def _pre_close(bars: pd.DataFrame, index: int) -> float | None:
    row = bars.iloc[index]
    value = row.get("pre_close")
    if value is not None and not pd.isna(value):
        return float(value)
    if index > 0:
        previous = bars.iloc[index - 1].get("close")
        if previous is not None and not pd.isna(previous):
            return float(previous)
    return None


def cost_adjusted_trade(bars: pd.DataFrame, simulation: dict[str, Any]) -> dict[str, Any]:
    """按成交约束、滑点和费用重算一笔候选策略交易。"""
    if not simulation.get("ok"):
        return {"filled": False, "reason": "simulation_not_filled", "net_return": None}
    try:
        entry_index = int(simulation["entry_index"])
        exit_index = int(simulation["exit_index"])
    except (KeyError, TypeError, ValueError):
        return {"filled": False, "reason": "missing_execution_index", "net_return": None}
    if not (0 <= entry_index < len(bars) and 0 <= exit_index < len(bars)):
        return {"filled": False, "reason": "execution_index_out_of_range", "net_return": None}

    entry = bars.iloc[entry_index]
    exit_row = bars.iloc[exit_index]
    exit_type = str(simulation.get("exit") or "")
    reference_exit = float(simulation.get("exit_price") or _number(exit_row, "open"))
    stop_price = reference_exit if exit_type == "stop" else None
    target_price = reference_exit if exit_type == "target" else None
    fill = simulate_round_trip(
        entry_open=_number(entry, "open"),
        entry_high=_number(entry, "high"),
        entry_low=_number(entry, "low"),
        entry_vol=_number(entry, "vol"),
        entry_pre_close=_pre_close(bars, entry_index),
        exit_open=reference_exit,
        exit_high=_number(exit_row, "high", reference_exit),
        exit_low=_number(exit_row, "low", reference_exit),
        exit_vol=_number(exit_row, "vol"),
        exit_pre_close=_pre_close(bars, exit_index),
        stop_price=stop_price,
        target_price=target_price,
        exit_day_low=_number(exit_row, "low", reference_exit),
        exit_day_high=_number(exit_row, "high", reference_exit),
    )
    result = fill.to_dict()
    result["gross_return"] = float(simulation.get("ret") or 0.0)
    result["net_return"] = round(fill.net_pnl / NOTIONAL, 8) if fill.filled else None
    result["entry_index"] = entry_index
    result["exit_index"] = exit_index
    return result


def summarize_costed_trades(records: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合净收益指标；回撤按固定名义资金交易序列保守复利计算。"""
    fills = [record["cost"] for record in records if record.get("cost", {}).get("filled")]
    net_returns = [float(fill["net_return"]) for fill in fills]
    positives = [value for value in net_returns if value > 0]
    negatives = [value for value in net_returns if value < 0]

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    ordered = sorted(
        (record for record in records if record.get("cost", {}).get("filled")),
        key=lambda record: (str(record.get("date") or ""), str(record.get("ts_code") or "")),
    )
    for record in ordered:
        equity *= 1.0 + float(record["cost"]["net_return"])
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, 1.0 - equity / peak)

    profit_factor = None
    if positives and negatives:
        profit_factor = sum(positives) / abs(sum(negatives))
    return {
        "net_n_trades": len(fills),
        "net_unfilled": len(records) - len(fills),
        "net_pnl": round(sum(float(fill["net_pnl"]) for fill in fills), 4),
        "net_avg_return": round(sum(net_returns) / len(net_returns), 6) if net_returns else None,
        "net_win_rate": round(len(positives) / len(net_returns), 4) if net_returns else None,
        "net_profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "net_max_drawdown": round(max_drawdown, 4) if net_returns else None,
        "commission": round(sum(float(fill["commission"]) for fill in fills), 4),
        "stamp_tax": round(sum(float(fill["stamp_tax"]) for fill in fills), 4),
        "other_fee": round(sum(float(fill["other_fee"]) for fill in fills), 4),
        "slippage_cost": round(sum(float(fill["slippage_cost"]) for fill in fills), 4),
    }


def promotion_metrics(oos_row: dict[str, Any]) -> dict[str, float | None]:
    """只读取净成本 OOS 指标；缺失时返回 None 并使门禁 fail-closed。"""
    def optional_float(name: str) -> float | None:
        value = oos_row.get(name)
        return float(value) if value is not None else None

    return {
        "profit_factor": optional_float("oos_net_profit_factor"),
        "win_rate": optional_float("oos_net_win_rate"),
        "max_drawdown": optional_float("oos_net_max_drawdown"),
    }
