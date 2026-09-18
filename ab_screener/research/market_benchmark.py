"""Fixed, snapshot-bound price-index comparisons; never risk-adjusted alpha."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import pandas as pd

from ab_screener.research.pit_reader import ResearchPitSnapshot

VERSION = "market-price-comparison-v1"
BENCHMARK_CODE = "000300.SH"
NOTICE = ("沪深300为不含分红再投资、未扣交易费用的价格指数参考；策略为扣费账户收益。"
          "收益差不是风险调整 alpha；未包含风格匹配指数与全收益指数验证。")


def price_window(snapshot: ResearchPitSnapshot, start: str, end: str) -> dict[str, Any]:
    """Include the preceding close and every session; no price forward filling."""
    if start > end or snapshot.benchmark_code != BENCHMARK_CODE:
        raise ValueError("基准身份或窗口无效")
    frame = snapshot.load_benchmark().sort_values("trade_date")
    if frame["trade_date"].duplicated().any():
        raise ValueError("基准日期重复")
    previous = frame[frame["trade_date"] < start].tail(1)
    window = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)]
    expected = set(snapshot.distinct_dates(start=start, end=end))
    if previous.empty or window.empty or not expected.issubset(set(window["trade_date"])):
        raise ValueError("基准缺少期初价格或研究交易日")
    if str(window.iloc[-1]["trade_date"]) != end:
        raise ValueError("基准缺少期末报价，禁止截短窗口")
    prices = pd.to_numeric(pd.concat([previous, window])["close"], errors="coerce")
    if any(not Decimal(str(value)).is_finite() or value <= 0 for value in prices):
        raise ValueError("基准价格缺失或无效")
    initial = Decimal(str(previous.iloc[0]["close"]))
    peak = Decimal(1)
    curve: list[dict[str, Any]] = []
    for row in window.to_dict("records"):
        nav = Decimal(str(row["close"])) / initial
        peak = max(peak, nav)
        curve.append({"trade_date": str(row["trade_date"]), "benchmark_nav": float(nav),
                      "benchmark_drawdown": float(1 - nav / peak)})
    return {"start": start, "end": end, "anchor_date": str(previous.iloc[0]["trade_date"]),
            "sessions": len(window), "benchmark_return": curve[-1]["benchmark_nav"] - 1,
            "benchmark_max_drawdown": max(row["benchmark_drawdown"] for row in curve),
            "curve": curve}


def compare_account(snapshot: ResearchPitSnapshot, start: str, end: str,
                    details: dict[str, Any], strategy_return: float | None,
                    stress_return: float | None = None) -> dict[str, Any]:
    """Only leading cash days can be filled; missing invested valuations fail."""
    result = price_window(snapshot, start, end)
    initial = int(details["initial_equity_fen"])
    points = details["equity_curve"]
    if initial <= 0 or not points or strategy_return is None:
        raise ValueError("缺少可核验的策略账户权益")
    mapping = {str(row["trade_date"]): row for row in points}
    if len(mapping) != len(points) or any(day < start or day > end for day in mapping):
        raise ValueError("策略权益日期重复或超出研究窗口")
    first = min(mapping)
    if any(str(e.get("trade_date", e.get("date", first))) < first
           for e in details.get("events", []) if e.get("filled")):
        raise ValueError("首次估值之前已存在成交，不能补为现金")
    expected_final = Decimal(int(details["final_equity_fen"])) / Decimal(initial) - 1
    if abs(expected_final - Decimal(str(strategy_return))) > Decimal("0.000000011"):
        raise ValueError("策略净收益与账户末值不一致")
    peak = Decimal(1)
    for row in result["curve"]:
        day = row["trade_date"]
        if day < first:
            equity = initial
        elif day not in mapping:
            raise ValueError(f"策略缺少 {day} 估值，不能填充")
        else:
            equity = int(mapping[day]["equity_fen"])
        if equity < 0:
            raise ValueError("策略负权益")
        nav = Decimal(equity) / Decimal(initial)
        peak = max(peak, nav)
        row.update(strategy_nav=float(nav), strategy_drawdown=float(1 - nav / peak),
                   excess_return=float(nav) - row["benchmark_nav"])
    if int(mapping[end]["equity_fen"]) != int(details["final_equity_fen"]):
        raise ValueError("期末权益不一致")
    result.update(strategy_return=float(expected_final),
                  strategy_max_drawdown=max(row["strategy_drawdown"] for row in result["curve"]),
                  excess_return=float(expected_final) - result["benchmark_return"],
                  stress_excess_return=(stress_return - result["benchmark_return"]
                                        if stress_return is not None else None))
    return result


def build_market_comparison(snapshot: ResearchPitSnapshot, windows: dict[str, Any],
                            details: dict[str, Any], selected: dict[str, Any],
                            stress_return: float | None, wf: dict[str, Any] | None) -> dict[str, Any]:
    from ab_screener.research.portfolio_metric_contract import portfolio_total_return

    result: dict[str, Any] = {"version": VERSION, "benchmark_code": BENCHMARK_CODE,
                             "benchmark_name": "沪深300价格指数", "notice": NOTICE,
                             "snapshot": snapshot.identity(), "status": "INSUFFICIENT"}
    try:
        for scope in ("is", "oos"):
            result[scope] = compare_account(
                snapshot, windows[scope][0], windows[scope][1], details.get(scope, {}),
                portfolio_total_return(selected[scope]), stress_return if scope == "oos" else None)
        wf_rows = (wf or {}).get("wf_detail", [])
        result["wf"] = []
        for index, window in enumerate(windows["wf"]):
            reference = price_window(snapshot, window["test_start"], window["test_end"])
            metrics = wf_rows[index] if index < len(wf_rows) else {}
            value = metrics.get("test_net_total_return")
            result["wf"].append({"window": f"WF{index + 1}",
                                   "benchmark_return": reference["benchmark_return"],
                                   "strategy_return": value,
                                   "excess_return": (value - reference["benchmark_return"]
                                                     if value is not None else None),
                                   "test_n": metrics.get("test_n")})
        result["status"] = "COMPLETE"
    except (ValueError, KeyError, TypeError) as exc:
        result["reason"] = f"基准对照证据不足：{exc}"
    blob = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    result["sha256"] = hashlib.sha256(blob.encode()).hexdigest()
    return result
