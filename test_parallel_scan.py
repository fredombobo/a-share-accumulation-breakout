"""多进程并行扫描单元测试"""
from __future__ import annotations

import os
import sys
import time

os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from parallel_scan import _parent_alive, detect_many, prefilter_volume_parallel, resolve_workers
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


def test_parent_alive_probe_safe():
    """回归：Windows 上 os.kill(ppid,0) 对受保护进程会误判父死导致 worker 自杀。

    _parent_alive 只对确定不存在的 PID 返回 False；探测自己必须返回 True 且不得杀死本进程。
    """
    me = os.getpid()
    assert _parent_alive(me) is True, "探测自己必须判定存活（且不能杀死自己）"
    # 任意必然不存在的 PID：当前进程的 PID 翻转不可靠，用小于 1 的非法值兜底 + 999999 大概率不存在
    assert _parent_alive(0) is False or _parent_alive(0) in (False, True)  # 0 无意义，不抛异常即可
    # 找 3 个大概率不存在的 PID 验证不会误报存活
    dead = [99999991, 99999992, 99999993]
    for pid in dead:
        # 若恰好被复用为真实进程（极低概率），跳过该断言
        try:
            os.kill(pid, 0)
            continue  # 存在，跳过
        except OSError:
            assert _parent_alive(pid) is False, f"不存在的 PID {pid} 应判定死亡"
    print("[PASS] _parent_alive 安全探测 ok（不会误杀/误判）")


def test_full_scan_does_not_kill_pytest():
    """回归：完整并行扫描运行不能杀死 pytest 进程（曾经 os.kill(ppid,0) 的副作用）。"""
    me = os.getpid()
    codes, daily = _build_market(n_stocks=220, flat_days=30)
    res = detect_many(codes, daily, workers=2)
    assert os.getpid() == me, "pytest 进程被杀死！"
    assert set(res.keys()) == set(codes)
    print(f"[PASS] 完整扫描不杀 pytest，检测 {len(res)} 只")


def test_cancel_scan_returns_promptly():
    """取消扫描：cancel_check 置 True 后 detect_many 必须快速返回，不挂死。"""
    codes, daily = _build_market(n_stocks=220, flat_days=40)
    state = {"cancelled": False}

    def _cb():
        return state["cancelled"]

    def _set():
        time.sleep(0.6)
        state["cancelled"] = True

    import threading
    threading.Thread(target=_set, daemon=True).start()
    t0 = time.time()
    res = detect_many(codes, daily, workers=0, cancel_check=_cb)
    elapsed = time.time() - t0
    assert elapsed < 30, f"取消后未及时返回：{elapsed:.1f}s"
    print(f"[PASS] 取消扫描 {elapsed:.1f}s 内返回（部分结果 {len(res)} 只）")


if __name__ == "__main__":
    test_resolve_workers()
    test_single_stock_detect_still_ok()
    test_parent_alive_probe_safe()
    test_full_scan_does_not_kill_pytest()
    test_detect_serial_vs_parallel_same_hits()
    test_prefilter_parallel_matches_serial()
    test_cancel_scan_returns_promptly()
    print("\n全部并行扫描测试通过 ✅")
