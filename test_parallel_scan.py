"""多进程并行扫描单元测试"""
from __future__ import annotations

import os
import sys
import time

os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from parallel_scan import detect_many, prefilter_volume_parallel, resolve_workers
from signals import detect_accumulation_breakout
from test_signals import make_synthetic


def _build_market(n_stocks: int = 80, flat_days: int = 40) -> tuple[list[str], pd.DataFrame]:
    """合成多只股票日线（部分为可突破形态）。"""
    rows = []
    codes = []
    for i in range(n_stocks):
        code = f"{i:06d}.SZ"
        codes.append(code)
        # 每 4 只里 1 只做可突破横盘，其余做随机噪声
        if i % 4 == 0:
            df = make_synthetic(seed=100 + i, flat_days=flat_days)
            for _, r in df.iterrows():
                td = str(r["date"]).replace("-", "")[:8]
                rows.append({
                    "ts_code": code,
                    "trade_date": td,
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "vol": r["vol"],
                })
        else:
            rng = np.random.default_rng(i)
            dates = pd.bdate_range("2025-10-01", periods=flat_days + 1)
            base = 10 + rng.normal(0, 0.5)
            for j, d in enumerate(dates):
                c = base + rng.normal(0, 0.3) + j * 0.02  # 带趋势，不易横盘突破
                rows.append({
                    "ts_code": code,
                    "trade_date": d.strftime("%Y%m%d"),
                    "open": c,
                    "high": c * 1.01,
                    "low": c * 0.99,
                    "close": c,
                    "vol": 1000 + rng.integers(0, 200),
                })
    daily = pd.DataFrame(rows).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return codes, daily


def test_resolve_workers():
    w = resolve_workers(0)
    assert w >= 1
    assert resolve_workers(1) == 1
    assert resolve_workers(4) == 4
    print(f"[PASS] resolve_workers auto={w}")


def test_detect_serial_vs_parallel_same_hits():
    # ≥200 才走进程池；用 220 验证一致性
    codes, daily = _build_market(n_stocks=220, flat_days=45)
    t0 = time.time()
    serial = detect_many(codes, daily, workers=1)
    t_serial = time.time() - t0
    t1 = time.time()
    parallel = detect_many(codes, daily, workers=0)
    t_par = time.time() - t1

    assert set(serial.keys()) == set(parallel.keys()) == set(codes)
    s_hits = {c for c, s in serial.items() if s.get("is_breakout")}
    p_hits = {c for c, s in parallel.items() if s.get("is_breakout")}
    assert s_hits == p_hits, f"hits mismatch serial={len(s_hits)} parallel={len(p_hits)}"
    assert len(s_hits) >= 1, "合成数据应有突破命中"
    print(f"[PASS] serial/parallel 命中一致 hits={len(s_hits)}  serial={t_serial:.2f}s parallel={t_par:.2f}s")


def test_prefilter_parallel_matches_serial():
    codes, daily = _build_market(n_stocks=220, flat_days=30)
    from prefilter_fast import volume_breakout_candidates

    a = volume_breakout_candidates(daily, codes)
    b = prefilter_volume_parallel(daily, codes, workers=0)
    assert a == b, f"prefilter mismatch only_a={a-b} only_b={b-a}"
    print(f"[PASS] prefilter parallel==serial keep={len(a)}")


def test_single_stock_detect_still_ok():
    df = make_synthetic(seed=1, flat_days=50)
    sig = detect_accumulation_breakout(df)
    assert sig["is_breakout"] is True
    print("[PASS] single detect ok")


if __name__ == "__main__":
    test_resolve_workers()
    test_single_stock_detect_still_ok()
    test_detect_serial_vs_parallel_same_hits()
    test_prefilter_parallel_matches_serial()
    print("\n全部并行扫描测试通过 ✅")
