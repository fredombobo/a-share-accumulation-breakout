"""网格参数优化器（P4）

核心架构：信号缓存 + 参数重放解耦
- 入场检测与出场参数解耦：每只股票每个采样日只 detect 一次（B 方案按 vol_ratio_min 分档），
  缓存信号；108 组出场参数只重放轻量 simulate_trade，避免 27 万×108 次重复检测。
- 并行：复用 parallel_scan 的 spawn 进程池模式（worker 载荷可 pickle、父进程看门狗）。

输出：每组参数一行统计（DataFrame），并写入 param_eval（P5 接入）。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, wait

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("PYTHONPATH", None)

from config import BT_MIN_TRADES, GRID_BENCH
from trade_sim import simulate_trade, summarize

_MIN_CODES_FOR_POOL = 100


def param_id(strategy: str, params: dict) -> str:
    """参数组合的稳定 hash 主键。"""
    blob = json.dumps({"strategy": strategy, **params}, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:16]


def grid_combos(strategy: str, grid: dict | None = None) -> list[dict]:
    """展开网格为参数组合列表。"""
    g = grid or GRID_BENCH
    keys = sorted(g.keys())
    out = []
    for vals in itertools.product(*(g[k] for k in keys)):
        out.append({"strategy": strategy, **dict(zip(keys, vals))})
    return out


def _detect_signals_for_code(
    df: pd.DataFrame,
    sample_days: list[str],
    cal_index: dict[str, int],
    cal: list[str],
    horizon: int,
    strategy: str,
    vr_levels: list[float],
) -> list[dict]:
    """单只股票的信号缓存：逐采样日检测入场，返回信号列表。

    每项: {day, entry_i, bench_vols: {vr: bench_vol}}
    A 方案：detect_accumulation_breakout（与参数无关，detect 一次）
    B 方案：detect_plan_b（vol_ratio_min 影响建仓识别，按 vr_levels 各 detect 一次）
    """
    from bench_volume import find_build_seqs
    from entry_plan_b import detect_plan_b
    from signals import detect_accumulation_breakout

    dts = df["trade_date"].astype(str).tolist()
    dts_set = set(dts)
    signals: list[dict] = []

    for day in sample_days:
        day_i = cal_index.get(day, -1)
        if day_i < 60:
            continue
        win_start = cal[max(0, day_i - horizon)]
        win = df[(df["trade_date"] >= win_start) & (df["trade_date"] <= day)]
        if len(win) < 60:
            continue

        if strategy == "A":
            sig = detect_accumulation_breakout(win)
            if not sig.get("is_breakout"):
                continue
            bd = "".join(ch for ch in str(sig.get("breakout_date") or "") if ch.isdigit())[:8]
            recent = {str(x) for x in cal[max(0, day_i - 5): day_i + 1]}
            if not bd or bd not in recent or bd not in dts_set:
                continue
            entry_i = dts.index(bd) + 1
            if entry_i >= len(df):
                continue
            bo_vol = float(df.loc[df["trade_date"] == bd, "vol"].iloc[0])
            bench_vols = {}
            for vr in vr_levels:
                seqs = find_build_seqs(win, vol_ratio_min=vr)
                bench_vols[vr] = seqs[-1]["bench_vol"] if seqs else bo_vol
            signals.append({"day": day, "entry_i": entry_i, "bench_vols": bench_vols})

        else:  # strategy B
            for vr in vr_levels:
                sig = detect_plan_b(win, vol_ratio_min=vr)
                if not sig.get("is_breakout"):
                    continue
                bd = "".join(ch for ch in str(sig.get("breakout_date") or "") if ch.isdigit())[:8]
                if bd not in dts_set:
                    continue
                entry_i = dts.index(bd) + 1
                if entry_i >= len(df):
                    continue
                signals.append({"day": day, "entry_i": entry_i,
                                "bench_vols": {vr: sig["bench_vol"]}, "vr": vr})
    return signals


def _replay_params(df: pd.DataFrame, signals: list[dict], combos: list[dict]) -> dict[str, list[dict]]:
    """对缓存信号按 108 组出场参数重放模拟。返回 {pid: [trades]}。"""
    out: dict[str, list[dict]] = {}
    for combo in combos:
        pid = param_id(combo["strategy"], combo)
        trades = out.setdefault(pid, [])
        for s in signals:
            if combo["strategy"] == "B" and s.get("vr") != combo.get("vol_ratio_min"):
                continue  # B 方案信号与该 vr 档位绑定
            bv = s["bench_vols"].get(combo.get("vol_ratio_min"))
            if not bv:
                continue
            sim = simulate_trade(df, s["entry_i"], mode="bench", params={
                "bench_vol": bv,
                "stop_pct": combo["stop_pct"],
                "exit_window": combo["exit_window"],
                "strong_reset": combo["strong_reset"],
            })
            if sim.get("ok"):
                trades.append({"ret": sim["ret"], "win": sim["win"], "exit": sim["exit"],
                               "days": sim["days"], "max_dd": sim.get("max_dd"),
                               "ts_code": str(df["ts_code"].iloc[0]) if "ts_code" in df else "",
                               "date": s["day"]})
    return out


def _worker_chunk(payload: tuple) -> dict[str, list[dict]]:
    """子进程：对股票分片跑「信号缓存 + 全参数重放」。"""
    codes, chunk_df, sample_days, cal, horizon, strategy, vr_levels, combos = payload
    cal_index = {d: i for i, d in enumerate(cal)}
    merged: dict[str, list[dict]] = {}
    for code in codes:
        df = chunk_df[chunk_df["ts_code"] == code].sort_values("trade_date").reset_index(drop=True)
        if len(df) < 80:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["vol"] = pd.to_numeric(df.get("vol", df.get("volume")), errors="coerce")
        signals = _detect_signals_for_code(df, sample_days, cal_index, cal, horizon, strategy, vr_levels)
        if not signals:
            continue
        for pid, trades in _replay_params(df, signals, combos).items():
            merged.setdefault(pid, []).extend(trades)
    return merged


def run_grid(
    start: str,
    end: str,
    strategy: str = "A",
    step: int = 5,
    max_codes: int | None = None,
    horizon: int = 160,
    grid: dict | None = None,
    workers: int | None = None,
    progress_cb=None,
) -> pd.DataFrame:
    """网格优化主入口。返回每组参数一行统计的 DataFrame（按 profit_factor 降序）。"""
    from local_store import LocalStore
    from parallel_scan import resolve_workers

    store = LocalStore()
    basic = store.load_stock_basic()
    codes = basic["ts_code"].astype(str).tolist()
    codes = [c for c in codes if c.endswith((".SH", ".SZ")) and not c.startswith(("4", "8", "92"))]
    if max_codes:
        codes = codes[:max_codes]

    # 交易日历用全库（检测窗口需要回测区间之前的日期索引），区间内按 step 采样
    cal = store.distinct_dates("daily")
    sample_days = [d for d in cal if start <= d <= end][:: max(1, step)]
    if not sample_days:
        return pd.DataFrame()
    combos = grid_combos(strategy, grid)
    vr_levels = sorted({c["vol_ratio_min"] for c in combos})
    if progress_cb:
        progress_cb(f"优化池 {len(codes)} 只 × {len(sample_days)} 采样日 × {len(combos)} 组合", 5)

    # 加载区间前置扩展：箱体/建仓序列判定需要窗口前 horizon 日数据
    load_start = (pd.to_datetime(start) - pd.Timedelta(days=365)).strftime("%Y%m%d")
    big = store.load_daily(ts_codes=codes, start=load_start, end=end)
    if big.empty:
        return pd.DataFrame()

    nw = resolve_workers(workers)
    results: dict[str, list[dict]] = {}
    if len(codes) < _MIN_CODES_FOR_POOL or nw <= 1:
        r = _worker_chunk((codes, big, sample_days, cal, horizon, strategy, vr_levels, combos))
        results = r
    else:
        chunk_size = max(50, (len(codes) + nw - 1) // nw)
        chunks = [codes[i: i + chunk_size] for i in range(0, len(codes), chunk_size)]
        pool = ProcessPoolExecutor(max_workers=nw)
        try:
            futs = [pool.submit(_worker_chunk, (ch, big[big["ts_code"].isin(ch)].copy(),
                                              sample_days, cal, horizon, strategy, vr_levels, combos))
                    for ch in chunks]
            done = 0
            pending = set(futs)
            while pending:
                finished, pending = wait(pending, timeout=2.0, return_when="FIRST_COMPLETED")
                for fut in finished:
                    try:
                        for pid, trades in fut.result().items():
                            results.setdefault(pid, []).extend(trades)
                    except Exception as e:  # noqa: BLE001
                        print(f"[optimizer][warn] 分片失败: {e}")
                    done += 1
                    if progress_cb:
                        progress_cb(f"分片 {done}/{len(chunks)}", 5 + int(90 * done / len(chunks)))
        finally:
            pool.shutdown(wait=True)

    # 聚合统计
    rows = []
    combo_map = {param_id(c["strategy"], c): c for c in combos}
    for pid, trades in results.items():
        s = summarize(trades)
        if not s.get("n_trades"):
            continue
        rows.append({"param_id": pid, **combo_map[pid], **s})
    df_out = pd.DataFrame(rows)
    if not df_out.empty:
        df_out = df_out[df_out["n_trades"] >= BT_MIN_TRADES]  # 统计功效门槛
        df_out = df_out.sort_values("profit_factor", ascending=False).reset_index(drop=True)
    return df_out


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20250101")
    p.add_argument("--end", default="20260731")
    p.add_argument("--strategy", default="A", choices=["A", "B"])
    p.add_argument("--step", type=int, default=10)
    p.add_argument("--max-codes", type=int, default=200)
    args = p.parse_args()
    df = run_grid(start=args.start, end=args.end, strategy=args.strategy,
                  step=args.step, max_codes=args.max_codes,
                  progress_cb=lambda m, pct: print(f"[{pct:3d}%] {m}"))
    pd.set_option("display.width", 200)
    print(df.head(15).to_string() if not df.empty else "无有效组合（样本不足或无信号）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
