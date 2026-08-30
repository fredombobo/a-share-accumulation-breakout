"""交易模拟器（双模式）

- mode="fixed"：旧固定规则（止损 -stop_pct / 止盈 +target_pct / 最长 max_hold 日），
  与 backtest_signals._simulate_trade 逐数字一致（作为对比基线，P2 回归验证用）。
- mode="bench"：标杆量二次出货出场（bench_volume），可选止盈、固定止损兜底
  与最长持有强平。优先级（保守序）：stop → target → bench → time。

入场统一（ENTRY-DEFINITION-V1）：
  entry_i = **信号日**索引；成交价 = 下一交易日开盘（无 open 用 close）。
  见 ab_screener.domain.entry_definition。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ab_screener.domain.entry_definition import entry_price_from_bars
from bench_volume import bench_exit_events
from config import (
    BENCH_EXIT_WINDOW,
    BENCH_MAX_HOLD_DAYS,
    BENCH_STOP_PCT,
    BENCH_STRONG_RESET,
    MAX_HOLD_DAYS,
    STOP_LOSS_PCT,
    TARGET_PCT_1,
)


def _entry_price(bars: pd.DataFrame, entry_i: int) -> float | None:
    """entry_i 为信号日索引；委托 ENTRY v1。"""
    return entry_price_from_bars(bars, entry_i)


def simulate_trade(
    bars: pd.DataFrame,
    entry_i: int,
    mode: str = "fixed",
    params: dict | None = None,
) -> dict:
    """返回 {ok, ret, days, exit, win, entry, exit_price, max_dd}。"""
    p = params or {}
    entry = _entry_price(bars, entry_i)
    if entry is None:
        return {"ok": False}
    execution_i = entry_i + 1

    if mode == "fixed":
        stop_pct = p.get("stop_pct", STOP_LOSS_PCT)
        target_pct = p.get("target_pct", TARGET_PCT_1)
        max_hold = p.get("max_hold", MAX_HOLD_DAYS)
        stop = entry * (1 - stop_pct)
        target = entry * (1 + target_pct)
        # A 股股票 T+1：execution_i 是买入日，最早只能在下一交易日卖出。
        for j in range(execution_i + 1, min(len(bars), entry_i + 1 + max_hold)):
            row = bars.iloc[j]
            lo, hi, cl = float(row["low"]), float(row["high"]), float(row["close"])
            if lo <= stop:  # 先止损再止盈（保守）
                ret = stop / entry - 1
                return {
                    "ok": True,
                    "ret": ret,
                    "days": j - entry_i,
                    "exit": "stop",
                    "win": ret > 0,
                    "entry": entry,
                    "exit_price": stop,
                    "entry_index": execution_i,
                    "exit_index": j,
                }
            if hi >= target:
                ret = target / entry - 1
                return {
                    "ok": True,
                    "ret": ret,
                    "days": j - entry_i,
                    "exit": "target",
                    "win": True,
                    "entry": entry,
                    "exit_price": target,
                    "entry_index": execution_i,
                    "exit_index": j,
                }
            if j == min(len(bars), entry_i + 1 + max_hold) - 1:
                ret = cl / entry - 1
                return {
                    "ok": True,
                    "ret": ret,
                    "days": j - entry_i,
                    "exit": "time",
                    "win": ret > 0,
                    "entry": entry,
                    "exit_price": cl,
                    "entry_index": execution_i,
                    "exit_index": j,
                }
        return {"ok": False}

    if mode == "bench":
        bench_vol = p.get("bench_vol")
        if not bench_vol:
            return {"ok": False, "reason": "bench 模式需要 params['bench_vol']"}
        stop_pct = p.get("stop_pct", BENCH_STOP_PCT)
        target_pct = p.get("target_pct")
        max_hold = p.get("max_hold", BENCH_MAX_HOLD_DAYS)
        stop = entry * (1 - stop_pct)
        target = entry * (1 + float(target_pct)) if target_pct is not None else None
        ev = bench_exit_events(
            bars,
            entry_i,
            bench_vol,
            exit_window=p.get("exit_window", BENCH_EXIT_WINDOW),
            strong_reset=p.get("strong_reset", BENCH_STRONG_RESET),
            max_hold=max_hold,
        )
        exit_j, exit_type = ev["exit_j"], ev["exit_type"]
        peak = entry
        max_dd = 0.0
        # 买入日内即使触发止损也不可卖出；从下一交易日起检查卖出条件。
        for j in range(execution_i + 1, exit_j + 1):
            row = bars.iloc[j]
            lo, hi, cl = float(row["low"]), float(row["high"]), float(row["close"])
            if lo <= stop:  # 止损优先（保守）
                ret = stop / entry - 1
                return {
                    "ok": True,
                    "ret": ret,
                    "days": j - entry_i,
                    "exit": "stop",
                    "win": False,
                    "entry": entry,
                    "exit_price": stop,
                    "max_dd": round(max_dd, 4),
                    "entry_index": execution_i,
                    "exit_index": j,
                }
            if target is not None and hi >= target:
                ret = target / entry - 1
                return {
                    "ok": True,
                    "ret": ret,
                    "days": j - entry_i,
                    "exit": "target",
                    "win": True,
                    "entry": entry,
                    "exit_price": target,
                    "max_dd": round(max_dd, 4),
                    "entry_index": execution_i,
                    "exit_index": j,
                }
            peak = max(peak, float(row["high"]))
            max_dd = max(max_dd, 1 - cl / peak)
        # bench 出场：信号确认于 exit_j 收盘，必须次日开盘卖出；禁止无次日时
        # 回退到确认日收盘。time 出场也必须至少晚于买入日。
        if exit_type == "bench" and exit_j + 1 < len(bars):
            op = bars.iloc[exit_j + 1].get("open")
            px = float(op) if op and not pd.isna(op) else float(bars.iloc[exit_j + 1]["close"])
            days = exit_j + 1 - entry_i
            exit_index = exit_j + 1
        else:
            if exit_type == "bench" or exit_j <= execution_i:
                return {"ok": False, "reason": "NO_T1_EXIT_BAR"}
            px = float(bars.iloc[exit_j]["close"])
            days = exit_j - entry_i
            exit_index = exit_j
        ret = px / entry - 1
        return {
            "ok": True,
            "ret": ret,
            "days": days,
            "exit": exit_type,
            "win": ret > 0,
            "entry": entry,
            "exit_price": px,
            "max_dd": round(max_dd, 4),
            "entry_index": execution_i,
            "exit_index": exit_index,
        }

    raise ValueError(f"未知 mode: {mode}")


def summarize(trades: list[dict]) -> dict:
    """聚合统计：胜率/盈亏比/平均收益/最大回撤（交易级）。"""
    if not trades:
        return {"n_trades": 0, "win_rate": None, "avg_ret": None, "profit_factor": None, "max_drawdown": None}
    rets = np.array([t["ret"] for t in trades], dtype=float)
    wins = np.array([t["win"] for t in trades], dtype=bool)
    dds = np.array([t.get("max_dd") or 0.0 for t in trades], dtype=float)
    out = {
        "n_trades": len(trades),
        "win_rate": round(float(wins.mean()), 4),
        "avg_ret": round(float(rets.mean()), 4),
        "median_ret": round(float(np.median(rets)), 4),
        "avg_win": round(float(rets[wins].mean()), 4) if wins.any() else None,
        "avg_loss": round(float(rets[~wins].mean()), 4) if (~wins).any() else None,
        "profit_factor": None,
        "max_drawdown": round(float(dds.max()), 4) if len(dds) else None,
        "exits": pd.Series([t["exit"] for t in trades]).value_counts().to_dict(),
    }
    if wins.any() and (~wins).any() and rets[~wins].sum() != 0:
        out["profit_factor"] = round(float(rets[wins].sum() / abs(rets[~wins].sum())), 3)
    return out
