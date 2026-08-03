"""
轻量信号回测
============
在本地 SQLite 日线上，对历史交易日抽样跑 strict 信号，按固定进出场规则统计胜率。

用法：
  python backtest_signals.py --start 20250101 --end 20260731 --step 5 --max-codes 400
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from config import OUT_DIR  # noqa: E402
from local_store import LocalStore  # noqa: E402
from signals import detect_accumulation_breakout  # noqa: E402


def _simulate_trade(bars: pd.DataFrame, entry_i: int, stop_pct: float = 0.07, target_pct: float = 0.12, max_hold: int = 15) -> dict:
    """entry_i 为信号日索引，次日开盘买入（若无 open 用 close）。"""
    if entry_i + 1 >= len(bars):
        return {"ok": False}
    entry = float(bars.iloc[entry_i + 1].get("open") or bars.iloc[entry_i + 1]["close"])
    stop = entry * (1 - stop_pct)
    target = entry * (1 + target_pct)
    for j in range(entry_i + 1, min(len(bars), entry_i + 1 + max_hold)):
        row = bars.iloc[j]
        lo = float(row["low"])
        hi = float(row["high"])
        cl = float(row["close"])
        # 先止损再止盈（保守）
        if lo <= stop:
            ret = stop / entry - 1
            return {"ok": True, "ret": ret, "days": j - entry_i, "exit": "stop", "win": ret > 0}
        if hi >= target:
            ret = target / entry - 1
            return {"ok": True, "ret": ret, "days": j - entry_i, "exit": "target", "win": True}
        if j == min(len(bars), entry_i + 1 + max_hold) - 1:
            ret = cl / entry - 1
            return {"ok": True, "ret": ret, "days": j - entry_i, "exit": "time", "win": ret > 0}
    return {"ok": False}


def run_backtest(
    start: str = "20250101",
    end: str = "20260731",
    step: int = 5,
    max_codes: int = 400,
    horizon: int = 160,
) -> dict:
    store = LocalStore()
    basic = store.load_stock_basic()
    if basic.empty:
        return {"error": "no stock_basic"}
    # 流动性粗筛：随机/截断代码集保持轻量
    codes = basic["ts_code"].astype(str).tolist()
    # 优先主板/创业板样本
    codes = [c for c in codes if c.endswith((".SH", ".SZ"))][:max_codes]

    cal = store.distinct_dates("daily")
    cal = [d for d in cal if start <= d <= end]
    sample_days = cal[:: max(1, step)]
    if not sample_days:
        return {"error": "no sample days"}
    cal_index = {d: i for i, d in enumerate(cal)}
    trades = []

    print(f"回测区间 {start}-{end} 采样日 {len(sample_days)} 股票 {len(codes)}")

    # 性能：每只股票一次性加载全区间日线，之后内存切片复用。
    # 原实现「采样日 × 股票」各发 2 次 load_daily（窗口+未来），100日×400股≈8万次查询。
    dfs_by_code: dict[str, pd.DataFrame] = {}
    for code in codes:
        df = store.load_daily(ts_codes=[code], start=start, end=end)
        if df is None or len(df) < 60:
            continue
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["vol"] = pd.to_numeric(df.get("vol", df.get("volume")), errors="coerce")
        dfs_by_code[code] = df
    print(f"加载日线 {len(dfs_by_code)}/{len(codes)} 只")

    processed = 0
    for code, df in dfs_by_code.items():
        processed += 1
        dts = df["trade_date"].astype(str).tolist()
        dts_set = set(dts)
        for day in sample_days:
            day_i = cal_index.get(day, -1)
            if day_i < 60:
                continue
            # 取 day 及之前 horizon 日（内存切片，不再查库）
            win_start = cal[max(0, day_i - horizon)]
            win = df[(df["trade_date"] >= win_start) & (df["trade_date"] <= day)]
            if len(win) < 60:
                continue
            sig = detect_accumulation_breakout(win)
            if not sig.get("is_breakout"):
                continue
            # 突破日必须落在采样日附近 5 个交易日内
            bd = "".join(ch for ch in str(sig.get("breakout_date") or "") if ch.isdigit())[:8]
            recent = set(str(x) for x in cal[max(0, day_i - 5): day_i + 1])
            if not bd or bd not in recent or bd not in dts_set:
                continue
            # 正确性：锚定突破日后一交易日入场（原实现误用「采样日+1」，突破早于采样日时收益口径偏）
            entry_i = dts.index(bd) + 1
            if entry_i >= len(df):
                continue
            sim = _simulate_trade(df, entry_i)
            if not sim.get("ok"):
                continue
            trades.append({
                "date": day,
                "ts_code": code,
                "ret": sim["ret"],
                "days": sim["days"],
                "exit": sim["exit"],
                "win": sim["win"],
            })
        if processed % 50 == 0:
            print(f"  … {processed}/{len(dfs_by_code)} 累计交易 {len(trades)}")

    if not trades:
        summary = {"n_trades": 0, "win_rate": None, "avg_ret": None, "msg": "无交易样本"}
    else:
        rets = np.array([t["ret"] for t in trades], dtype=float)
        wins = np.array([t["win"] for t in trades], dtype=bool)
        summary = {
            "n_trades": len(trades),
            "win_rate": round(float(wins.mean()), 4),
            "avg_ret": round(float(rets.mean()), 4),
            "median_ret": round(float(np.median(rets)), 4),
            "avg_win": round(float(rets[wins].mean()), 4) if wins.any() else None,
            "avg_loss": round(float(rets[~wins].mean()), 4) if (~wins).any() else None,
            "profit_factor": None,
            "exits": pd.Series([t["exit"] for t in trades]).value_counts().to_dict(),
            "params": {"stop_pct": 0.07, "target_pct": 0.12, "max_hold": 15, "entry": "next_open"},
        }
        if wins.any() and (~wins).any() and rets[~wins].sum() != 0:
            summary["profit_factor"] = round(float(rets[wins].sum() / abs(rets[~wins].sum())), 3)

    out_dir = os.path.join(OUT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"backtest_summary_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "trades_head": trades[:50]}, f, ensure_ascii=False, indent=2)
    md = os.path.join(out_dir, f"backtest_summary_{stamp}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# 轻量信号回测摘要\n\n")
        f.write(f"- 区间: {start} ~ {end}\n")
        f.write(f"- 交易数: {summary.get('n_trades')}\n")
        f.write(f"- 胜率: {summary.get('win_rate')}\n")
        f.write(f"- 平均收益: {summary.get('avg_ret')}\n")
        f.write(f"- 盈亏比(profit_factor): {summary.get('profit_factor')}\n")
        f.write(f"- 退出分布: {summary.get('exits')}\n")
        f.write("\n规则: 信号次日开盘买；止损-7%；止盈+12%；最长15日。\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"写入 {path}\n写入 {md}")
    return {"summary": summary, "path": path, "md": md}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20250101")
    p.add_argument("--end", default="20260731")
    p.add_argument("--step", type=int, default=5)
    p.add_argument("--max-codes", type=int, default=300)
    args = p.parse_args()
    run_backtest(start=args.start, end=args.end, step=args.step, max_codes=args.max_codes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
