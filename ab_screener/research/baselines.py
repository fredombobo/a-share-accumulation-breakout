"""固定种子随机基线与 20/60 均线基线。"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ab_screener.domain.costs import FillResult, simulate_round_trip, summarize_fills

if TYPE_CHECKING:
    from ab_screener.research.portfolio_accounting import PortfolioPolicy

RANDOM_SEED = 20260808


def random_baseline_trades(
    daily: pd.DataFrame,
    *,
    n_trades: int = 50,
    hold_days: int = 10,
    seed: int = RANDOM_SEED,
    entry_start: str | None = None,
    entry_end: str | None = None,
    codes: list[str] | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
    allowed_signal_dates: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """在相同日线宇宙上随机采样入场日/股票，走成本引擎。"""
    rng = random.Random(seed)
    if daily is None or daily.empty:
        return summarize_fills([])
    universe = sorted(set(codes if codes is not None else daily["ts_code"].astype(str).unique().tolist()))
    daily = daily[daily["ts_code"].astype(str).isin(universe)].copy()
    eligible: list[tuple[pd.DataFrame, int]] = []
    for code in universe:
        group = daily[daily["ts_code"].astype(str) == code].sort_values("trade_date").reset_index(drop=True)
        for index in range(1, len(group) - hold_days):
            signal_date = str(group.iloc[index - 1]["trade_date"])
            if allowed_signal_dates is not None and signal_date not in allowed_signal_dates:
                continue
            date = str(group.iloc[index]["trade_date"])
            exit_date = str(group.iloc[index + hold_days]["trade_date"])
            if entry_start and date < entry_start:
                continue
            if entry_end and (date > entry_end or exit_date > entry_end):
                continue
            eligible.append((group, index))
    fills: list[FillResult] = []
    candidates: list[dict[str, Any]] = []
    if eligible:
        sample = rng.sample(eligible, min(n_trades, len(eligible)))
    else:
        sample = []
    for g, i in sample:
        j = i + hold_days
        row_e, row_x = g.iloc[i], g.iloc[j]
        candidates.append(
            {
                "ts_code": str(row_e["ts_code"]),
                "date": str(g.iloc[i - 1]["trade_date"]),
                "entry_date": str(row_e["trade_date"]),
                "exit_date": str(row_x["trade_date"]),
                "exit": "time",
                "exit_price": float(row_x["open"]),
                "cost": {"filled": True},
            }
        )
        fills.append(
            simulate_round_trip(
                entry_open=float(row_e["open"]),
                entry_high=float(row_e["high"]),
                entry_low=float(row_e["low"]),
                entry_vol=float(row_e["vol"]),
                entry_pre_close=float(row_e["pre_close"]) if pd.notna(row_e.get("pre_close")) else None,
                exit_open=float(row_x["open"]),
                exit_high=float(row_x["high"]),
                exit_low=float(row_x["low"]),
                exit_vol=float(row_x["vol"]),
                exit_pre_close=float(row_x["pre_close"]) if pd.notna(row_x.get("pre_close")) else None,
            )
        )
    out = summarize_fills(fills)
    out["baseline"] = "random"
    out["seed"] = seed
    out["requested_trades"] = n_trades
    out["hold_days"] = hold_days
    out["entry_start"] = entry_start
    out["entry_end"] = entry_end
    out["universe_size"] = len(universe)
    _apply_portfolio_metrics(out, candidates, daily, portfolio_policy)
    return out


def ma_cross_baseline(
    daily: pd.DataFrame,
    *,
    fast: int = 20,
    slow: int = 60,
    hold_days: int = 10,
    max_trades: int = 80,
    entry_start: str | None = None,
    entry_end: str | None = None,
    codes: list[str] | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
    allowed_signal_dates: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """20/60 均线金叉入场，固定持有 hold_days，成本引擎。"""
    if daily is None or daily.empty:
        return summarize_fills([])
    universe = sorted(set(codes if codes is not None else daily["ts_code"].astype(str).unique().tolist()))
    daily = daily[daily["ts_code"].astype(str).isin(universe)].copy()
    fills: list[FillResult] = []
    candidates: list[dict[str, Any]] = []
    for code, g in daily.groupby("ts_code", sort=True):
        g = g.sort_values("trade_date").reset_index(drop=True)
        if len(g) < slow + hold_days + 2:
            continue
        close = pd.to_numeric(g["close"], errors="coerce")
        ma_f = close.rolling(fast).mean()
        ma_s = close.rolling(slow).mean()
        cross = (ma_f > ma_s) & (ma_f.shift(1) <= ma_s.shift(1))
        idxs = list(np.where(cross.fillna(False).to_numpy())[0])
        for i in idxs:
            # 信号日 i 收盘确认 → i+1 开盘买
            signal_date = str(g.iloc[i]["trade_date"])
            if allowed_signal_dates is not None and signal_date not in allowed_signal_dates:
                continue
            entry_i = i + 1
            exit_i = entry_i + hold_days
            if exit_i >= len(g):
                continue
            row_e, row_x = g.iloc[entry_i], g.iloc[exit_i]
            entry_date = str(row_e["trade_date"])
            exit_date = str(row_x["trade_date"])
            if entry_start and entry_date < entry_start:
                continue
            if entry_end and (entry_date > entry_end or exit_date > entry_end):
                continue
            candidates.append(
                {
                    "ts_code": str(code),
                    "date": str(g.iloc[i]["trade_date"]),
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "exit": "time",
                    "exit_price": float(row_x["open"]),
                    "cost": {"filled": True},
                }
            )
            fills.append(
                simulate_round_trip(
                    entry_open=float(row_e["open"]),
                    entry_high=float(row_e["high"]),
                    entry_low=float(row_e["low"]),
                    entry_vol=float(row_e["vol"]),
                    entry_pre_close=float(row_e["pre_close"]) if pd.notna(row_e.get("pre_close")) else None,
                    exit_open=float(row_x["open"]),
                    exit_high=float(row_x["high"]),
                    exit_low=float(row_x["low"]),
                    exit_vol=float(row_x["vol"]),
                    exit_pre_close=float(row_x["pre_close"]) if pd.notna(row_x.get("pre_close")) else None,
                )
            )
            if len(fills) >= max_trades:
                break
        if len(fills) >= max_trades:
            break
    out = summarize_fills(fills)
    out["baseline"] = f"ma{fast}/{slow}"
    out["requested_trades"] = max_trades
    out["hold_days"] = hold_days
    out["entry_start"] = entry_start
    out["entry_end"] = entry_end
    out["universe_size"] = len(universe)
    _apply_portfolio_metrics(out, candidates, daily, portfolio_policy)
    return out


def _apply_portfolio_metrics(
    out: dict[str, Any],
    candidates: list[dict[str, Any]],
    daily: pd.DataFrame,
    policy: PortfolioPolicy | None,
) -> None:
    if policy is None:
        return
    from ab_screener.research.portfolio_accounting import (
        portfolio_gate_metrics,
        simulate_portfolio,
    )

    trade_metrics = dict(out)
    portfolio = simulate_portfolio(candidates, daily, policy=policy)
    out.update({f"trade_{key}": value for key, value in trade_metrics.items()})
    out.update(portfolio_gate_metrics(portfolio))
