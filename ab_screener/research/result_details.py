"""Explain a selected integer-accounting path without recomputing trades."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any


def _decimal_ratio(numerator: int, denominator: int) -> str:
    return str(Decimal(numerator) / Decimal(denominator)) if denominator else "0"


def _public(value: Any) -> Any:
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, dict):
        return {key: str(item) if key.endswith(("_fen", "_micro")) else _public(item)
                for key, item in value.items()}
    return value


def build_details(portfolio: dict[str, Any], industry_by_code: dict[str, str]) -> dict[str, Any]:
    """Monthly P&L includes MTM; attribution explicitly covers realized P&L only."""
    initial = int(portfolio["portfolio_initial_equity_fen"])
    final = int(portfolio["portfolio_final_equity_fen"])
    curve = portfolio["equity_curve"]
    if curve and int(curve[-1]["equity_fen"]) != final:
        raise ValueError("权益末值与报告总资产不一致")
    monthly: list[dict[str, Any]] = []
    previous = initial
    for point in curve:
        month = str(point["trade_date"])[:6]
        if not monthly or monthly[-1]["month"] != month:
            monthly.append({"month": month, "start_equity_fen": previous})
        value = int(point["equity_fen"])
        monthly[-1].update(
            end_equity_fen=value,
            net_pnl_fen=value - monthly[-1]["start_equity_fen"],
            net_return=_decimal_ratio(value - monthly[-1]["start_equity_fen"], monthly[-1]["start_equity_fen"]),
        )
        previous = value
    stocks: dict[str, int] = defaultdict(int)
    industries: dict[str, int] = defaultdict(int)
    exits: dict[str, int] = defaultdict(int)
    realized = 0
    events = portfolio["events"]
    for event in events:
        if event["event"] != "EXIT_FILLED":
            continue
        pnl = int(event["realized_pnl_fen"])
        stocks[event["ts_code"]] += pnl
        industries[industry_by_code.get(event["ts_code"], "未分类")] += pnl
        exits[str(event.get("exit_type") or "未记录")] += pnl
        realized += pnl
    cash = initial + sum(int(event.get("cash_delta_fen", 0)) for event in events if event.get("filled"))
    if curve and cash != int(curve[-1]["cash_fen"]):
        raise ValueError("逐笔现金变动与末日现金不一致")
    if curve and sum(row["net_pnl_fen"] for row in monthly) != final - initial:
        raise ValueError("月度损益与总损益不一致")
    def contribution(values: dict[str, int]) -> list[dict[str, Any]]:
        return [{"name": key, "realized_pnl_fen": value} for key, value in sorted(values.items(), key=lambda item: -item[1])]

    return _public({
        "version": "selected-account-details-v1", "equity_sha256": portfolio["portfolio_equity_sha256"],
        "initial_equity_fen": initial, "final_equity_fen": final,
        "realized_pnl_fen": realized, "unrealized_pnl_fen": final - initial - realized,
        "equity_curve": curve, "monthly": monthly, "events": events,
        "stock_contribution": contribution(stocks), "industry_contribution": contribution(industries),
        "exit_contribution": contribution(exits), "reconciliation": "EXACT_FEN",
        "note": "曲线从首次模拟入场起；月度含浮盈亏。归因仅列已实现净损益，行业按运行时当前分类冻结，不是历史行业收益。",
    })
