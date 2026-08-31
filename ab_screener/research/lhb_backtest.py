"""点时正确的龙虎榜研究回测（T09）。默认下一开盘；涨停不可买。"""
from __future__ import annotations

import math
from typing import Any

from ab_screener.application.lhb_profiles import next_open_return
from ab_screener.domain.costs import COMMISSION_MIN, COMMISSION_RATE, OTHER_FEE_RATE, SLIPPAGE, STAMP_TAX_SELL
from ab_screener.domain.lhb_signal import SignalInput, evaluate_signal
from ab_screener.features.lhb_features import LhbSeatFact, select_pit_facts


def apply_costs(gross: float, *, side_roundtrip: bool = True) -> float:
    commission = max(COMMISSION_MIN / 100_000.0, COMMISSION_RATE) * 2
    stamp = STAMP_TAX_SELL
    other = OTHER_FEE_RATE * 2
    slip = SLIPPAGE * 2
    return gross - commission - stamp - other - slip


def max_drawdown(equity: list[float]) -> float:
    peak = equity[0] if equity else 0.0
    dd = 0.0
    for x in equity:
        peak = max(peak, x)
        if peak:
            dd = min(dd, x / peak - 1.0)
    return dd


def _mean_ci(xs: list[float]) -> dict[str, float]:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "low": float("nan"), "high": float("nan")}
    mu = sum(xs) / n
    if n == 1:
        return {"n": 1, "mean": mu, "low": mu, "high": mu}
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    se = math.sqrt(var / n)
    return {"n": n, "mean": mu, "low": mu - 1.96 * se, "high": mu + 1.96 * se}


def generate_historical_signal(
    facts: list[LhbSeatFact],
    inp: SignalInput,
    *,
    as_of: str,
) -> dict[str, Any]:
    """只用 as_of 可见事实；之后到达的数据不得改变历史信号。不改写披露时间。"""
    visible = select_pit_facts(facts, as_of=as_of)
    net = sum(f.net_fen for f in visible if f.ts_code == inp.ts_code) / 100.0
    filled = SignalInput(**{**inp.__dict__, "net_yuan": net})
    return evaluate_signal(filled)


def backtest_signals(
    signals: list[dict[str, Any]],
    *,
    bars: dict[str, dict[str, dict[str, Any]]],
    calendar: list[str],
    notional: float = 100_000.0,
) -> dict[str, Any]:
    grosses: list[float] = []
    nets: list[float] = []
    equity = [1.0]
    filled = 0
    unfillable = 0
    for sig in signals:
        ts = sig["ts_code"]
        res = next_open_return(
            bars.get(ts, {}),
            signal_date=sig["disclose_date"],
            calendar=calendar,
            horizon=1,
        )
        if res["status"] != "FILLED" or res["raw"] is None:
            unfillable += 1
            continue
        filled += 1
        g = float(res["raw"])
        n = apply_costs(g)
        grosses.append(g)
        nets.append(n)
        equity.append(equity[-1] * (1.0 + n * (notional / 100_000.0)))
    n = len(nets)
    mean_g = sum(grosses) / n if n else float("nan")
    mean_n = sum(nets) / n if n else float("nan")
    net_ci = _mean_ci(nets)
    return {
        "gross_return": mean_g,
        "net_return": mean_n,
        "benchmark_excess": None,
        "max_drawdown": max_drawdown(equity),
        "capacity_notional": notional,
        "sample_size": n,
        "filled": filled,
        "unfillable": unfillable,
        "ci": {
            "net_low": net_ci["low"],
            "net_high": net_ci["high"],
            "gross_low": _mean_ci(grosses)["low"],
            "gross_high": _mean_ci(grosses)["high"],
        },
    }
