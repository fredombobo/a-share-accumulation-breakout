"""交易模拟器（双模式）

- mode="fixed"：旧固定规则（止损 -stop_pct / 止盈 +target_pct / 最长 max_hold 日），
  与 backtest_signals._simulate_trade 逐数字一致（作为对比基线，P2 回归验证用）。
- mode="bench"：标杆量二次出货出场（bench_volume），固定止损兜底 + 最长持有强平。
  优先级（保守序）：stop → bench → time。

入场统一：信号日 entry_i 的次日开盘价（无 open 用 close）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

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
    if entry_i + 1 >= len(bars):
        return None
    nxt = bars.iloc[entry_i + 1]
    op = nxt.get("open")
    return float(op) if op and not pd.isna(op) else float(nxt["close"])


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

    if mode == "fixed":
        stop_pct = p.get("stop_pct", STOP_LOSS_PCT)
        target_pct = p.get("target_pct", TARGET_PCT_1)
        max_hold = p.get("max_hold", MAX_HOLD_DAYS)
        stop = entry * (1 - stop_pct)
        target = entry * (1 + target_pct)
        for j in range(entry_i + 1, min(len(bars), entry_i + 1 + max_hold)):
            row = bars.iloc[j]
            lo, hi, cl = float(row["low"]), float(row["high"]), float(row["close"])
            if lo <= stop:  # 先止损再止盈（保守）
                ret = stop / entry - 1
                return {"ok": True, "ret": ret, "days": j - entry_i, "exit": "stop",
                        "win": ret > 0, "entry": entry, "exit_price": stop}
            if hi >= target:
                ret = target / entry - 1
                return {"ok": True, "ret": ret, "days": j - entry_i, "exit": "target",
                        "win": True, "entry": entry, "exit_price": target}
            if j == min(len(bars), entry_i + 1 + max_hold) - 1:
                ret = cl / entry - 1
                return {"ok": True, "ret": ret, "days": j - entry_i, "exit": "time",
                        "win": ret > 0, "entry": entry, "exit_price": cl}
        return {"ok": False}

    if mode == "bench":
        bench_vol = p.get("bench_vol")
        if not bench_vol:
            return {"ok": False, "reason": "bench 模式需要 params['bench_vol']"}
        stop_pct = p.get("stop_pct", BENCH_STOP_PCT)
        max_hold = p.get("max_hold", BENCH_MAX_HOLD_DAYS)
        stop = entry * (1 - stop_pct)
        ev = bench_exit_events(
            bars, entry_i, bench_vol,
            exit_window=p.get("exit_window", BENCH_EXIT_WINDOW),
            strong_reset=p.get("strong_reset", BENCH_STRONG_RESET),
            max_hold=max_hold,
        )
        exit_j, exit_type = ev["exit_j"], ev["exit_type"]
        peak = entry
        max_dd = 0.0
        for j in range(entry_i + 1, exit_j + 1):
            row = bars.iloc[j]
            lo, cl = float(row["low"]), float(row["close"])
            if lo <= stop:  # 止损优先（保守）
                ret = stop / entry - 1
                return {"ok": True, "ret": ret, "days": j - entry_i, "exit": "stop",
                        "win": False, "entry": entry, "exit_price": stop,
                        "max_dd": round(max_dd, 4)}
            peak = max(peak, float(row["high"]))
            max_dd = max(max_dd, 1 - cl / peak)
        # bench 出场：信号确认于 exit_j 收盘，次日开盘卖出；无次日则当日收盘
        if exit_type == "bench" and exit_j + 1 < len(bars):
            op = bars.iloc[exit_j + 1].get("open")
            px = float(op) if op and not pd.isna(op) else float(bars.iloc[exit_j + 1]["close"])
            days = exit_j + 1 - entry_i
        else:
            px = float(bars.iloc[exit_j]["close"])
            days = exit_j - entry_i
        ret = px / entry - 1
        return {"ok": True, "ret": ret, "days": days, "exit": exit_type,
                "win": ret > 0, "entry": entry, "exit_price": px,
                "max_dd": round(max_dd, 4)}

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
