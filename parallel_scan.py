"""
多核心并行扫描
==============
CPU 密集：箱体扫描 detect_accumulation_breakout
策略：按股票代码切块 → ProcessPoolExecutor 多进程

Windows 兼容：spawn；worker 为模块顶层函数，可 pickle。
"""
from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, as_completed, wait
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

# 单进程兜底阈值：Windows spawn 有固定开销，票太少时多进程更慢
_MIN_CODES_FOR_POOL = 200

_WATCHDOG_STARTED = False


def _watch_parent_or_exit(interval: float = 5.0) -> None:
    """spawn 子进程看门狗：父进程死亡（强杀/异常退出）时自动退出，防止孤儿 worker 永久挂机。

    实测复现：父进程被 taskkill 后，worker 卡在任务队列等待、PID/内存不再变化、永不退出。
    在 worker 首次执行时启动 daemon 线程，每 interval 秒探测父进程存活，父死即 os._exit。
    """
    global _WATCHDOG_STARTED
    if _WATCHDOG_STARTED:
        return
    _WATCHDOG_STARTED = True
    import threading
    import time

    ppid = os.getppid()

    def _watch() -> None:
        while True:
            try:
                os.kill(ppid, 0)  # sig=0 仅探测存活（Windows 下 OpenProcess 检测）
            except OSError:
                os._exit(0)  # 父进程已死：立即退出（worker 无共享状态，无需清理）
            time.sleep(interval)

    threading.Thread(target=_watch, daemon=True).start()


def resolve_workers(workers: int | None = None) -> int:
    """workers<=0 或 None → 自动：cpu_count-1（至少 1，最多 16）。"""
    cpu = os.cpu_count() or 4
    auto = max(1, min(16, cpu - 1 if cpu > 2 else cpu))
    if workers is None or workers <= 0:
        return auto
    return max(1, int(workers))


def _chunk_list(items: list[str], n_chunks: int) -> list[list[str]]:
    if not items:
        return []
    n_chunks = max(1, min(n_chunks, len(items)))
    # 尽量均分，多进程负载均衡
    return [list(c) for c in np.array_split(items, n_chunks) if len(c) > 0]


def _ensure_date_col(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if "date" in df.columns:
        return df
    out = df
    if "trade_date" in df.columns:
        out = df.copy()
        out["date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
    return out


def _worker_detect_chunk(payload: tuple[list[str], pd.DataFrame, dict[str, Any]]) -> list[tuple[str, dict]]:
    """子进程：对一批代码做信号检测。"""
    _watch_parent_or_exit()  # 父死自退，防孤儿挂机
    codes, chunk_df, kwargs = payload
    # 子进程内再 import，避免 Windows spawn 循环导入问题
    from signals import detect_accumulation_breakout

    if chunk_df is None or chunk_df.empty or not codes:
        return []
    chunk_df = _ensure_date_col(chunk_df)
    grp = chunk_df.groupby("ts_code", sort=False)
    out: list[tuple[str, dict]] = []
    for code in codes:
        try:
            g = grp.get_group(code)
        except KeyError:
            continue
        try:
            sig = detect_accumulation_breakout(g, **(kwargs or {}))
        except Exception as e:  # noqa: BLE001
            sig = {"is_breakout": False, "reasons": [f"detect_error:{type(e).__name__}"], "box_days": 0}
        out.append((str(code), sig))
    return out


def _terminate_pool(pool: ProcessPoolExecutor) -> None:
    """尽力立即终止 ProcessPoolExecutor 的 worker 进程（不等当前分片跑完）。

    cancel 场景：用户点取消需要立即响应，不能等 worker 把 1000 只分片算完。
    worker 被 terminate 后对应 future 抛 BrokenProcessPool，由调用方按取消处理。
    """
    try:
        procs = getattr(pool, "_processes", None) or {}
        for p in procs.values():
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def detect_many(
    codes: Iterable[str],
    daily: pd.DataFrame,
    *,
    kwargs: dict[str, Any] | None = None,
    workers: int | None = None,
    progress_cb: Callable[[str, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    label: str = "信号检测",
) -> dict[str, dict]:
    """对多只股票并行检测，返回 {ts_code: sig}。

    workers=1 或代码数很少时走单进程（便于调试/小样本）。
    cancel_check：返回 True 时停止派发并释放进程池（当前分片跑完后退出）。
    """
    code_list = [str(c) for c in codes]
    if not code_list:
        return {}
    kwargs = dict(kwargs or {})
    n_workers = resolve_workers(workers)
    daily = _ensure_date_col(daily)

    def _prog(msg: str, pct: int = 0) -> None:
        if progress_cb:
            try:
                progress_cb(label, pct, msg)
            except Exception:  # noqa: BLE001
                pass

    # 只切需要的代码，减小 pickle
    sub = daily[daily["ts_code"].isin(set(code_list))]
    if sub is None or sub.empty:
        return {}

    if n_workers == 1 or len(code_list) < _MIN_CODES_FOR_POOL:
        _prog(f"单进程扫描 {len(code_list)} 只…", 25)
        pairs = _worker_detect_chunk((code_list, sub, kwargs))
        return {c: s for c, s in pairs}

    chunks = _chunk_list(code_list, n_workers)
    # 每个 worker 只拿自己的子集，避免全表复制 × N
    tasks: list[tuple[list[str], pd.DataFrame, dict[str, Any]]] = []
    for ch in chunks:
        ch_set = set(ch)
        cdf = sub[sub["ts_code"].isin(ch_set)]
        tasks.append((ch, cdf, kwargs))

    _prog(f"多进程×{len(tasks)} 扫描 {len(code_list)} 只…", 25)
    results: dict[str, dict] = {}
    cancelled = False
    pool = ProcessPoolExecutor(max_workers=len(tasks))
    futs = {pool.submit(_worker_detect_chunk, t): i for i, t in enumerate(tasks)}
    pending = set(futs)
    try:
        done = 0
        # wait(timeout=2) 轮询：每 2 秒检查一次取消（不再等整个分片完成才响应 cancel）
        while pending:
            finished, pending = wait(pending, timeout=2.0, return_when=FIRST_COMPLETED)
            for fut in finished:
                try:
                    pairs = fut.result()
                except Exception:  # noqa: BLE001  # worker 被终止/异常的分片跳过
                    pairs = []
                for c, s in pairs:
                    results[c] = s
                done += 1
                pct = 25 + int(70 * done / max(len(tasks), 1))
                _prog(f"完成分片 {done}/{len(tasks)}（累计 {len(results)} 只）", pct)
            if cancel_check is not None and cancel_check():
                cancelled = True
                _prog("已请求取消，正在停止…", 90)
                break
    finally:
        if cancelled:
            _terminate_pool(pool)  # 立即停 worker（不等当前分片）
            pool.shutdown(wait=False, cancel_futures=True)
        else:
            pool.shutdown(wait=True)

    if cancelled:
        _prog("扫描已取消", 100)
    return results


def _worker_prefilter_chunk(
    payload: tuple[list[str], pd.DataFrame, int, float, float],
) -> list[str]:
    """子进程：量能/近高点粗筛。"""
    _watch_parent_or_exit()  # 父死自退，防孤儿挂机
    codes, chunk_df, lookback, vol_ratio_min, near_high_pct = payload
    if chunk_df is None or chunk_df.empty or not codes:
        return []
    d = chunk_df.copy()
    d["vol"] = pd.to_numeric(d.get("vol", d.get("volume")), errors="coerce").fillna(0)
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d["high"] = pd.to_numeric(d["high"], errors="coerce")
    d = d.dropna(subset=["close"]).sort_values(["ts_code", "trade_date"])
    keep: list[str] = []
    code_set = set(codes)
    for code, g in d.groupby("ts_code", sort=False):
        if code not in code_set:
            continue
        g = g.tail(lookback)
        if len(g) < 10:
            continue
        vols = g["vol"].to_numpy(dtype=float)
        closes = g["close"].to_numpy(dtype=float)
        highs = g["high"].to_numpy(dtype=float)
        last_v = float(vols[-1])
        avg_v = float(vols[:-1].mean()) if len(vols) > 1 else 0.0
        last_c = float(closes[-1])
        hi = float(np.nanmax(highs)) if len(highs) else 0.0
        vol_ok = avg_v > 0 and last_v >= vol_ratio_min * avg_v
        if not vol_ok and len(vols) >= 6:
            tail_avg = float(vols[:-5].mean()) if len(vols) > 5 else avg_v
            if tail_avg > 0 and float(vols[-5:].max()) >= vol_ratio_min * tail_avg:
                vol_ok = True
        near_hi = hi > 0 and last_c >= hi * (1.0 - near_high_pct)
        if vol_ok or near_hi:
            keep.append(str(code))
    return keep


def prefilter_volume_parallel(
    daily: pd.DataFrame,
    codes: Iterable[str],
    *,
    lookback: int = 25,
    vol_ratio_min: float = 1.15,
    near_high_pct: float = 0.08,
    workers: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> set[str]:
    """并行量能/近高点预筛。"""
    code_list = [str(c) for c in codes]
    if not code_list or daily is None or daily.empty:
        return set(code_list)

    n_workers = resolve_workers(workers)
    sub = daily[daily["ts_code"].isin(set(code_list))]
    if sub.empty:
        return set()

    if n_workers == 1 or len(code_list) < _MIN_CODES_FOR_POOL:
        from prefilter_fast import volume_breakout_candidates

        return volume_breakout_candidates(
            sub, code_list, lookback=lookback, vol_ratio_min=vol_ratio_min, near_high_pct=near_high_pct
        )

    chunks = _chunk_list(code_list, n_workers)
    tasks = []
    for ch in chunks:
        cdf = sub[sub["ts_code"].isin(set(ch))]
        tasks.append((ch, cdf, lookback, vol_ratio_min, near_high_pct))

    keep: set[str] = set()
    cancelled = False
    pool = ProcessPoolExecutor(max_workers=len(tasks))
    futs = [pool.submit(_worker_prefilter_chunk, t) for t in tasks]
    pending = set(futs)
    try:
        while pending:
            finished, pending = wait(pending, timeout=2.0, return_when=FIRST_COMPLETED)
            for fut in finished:
                try:
                    keep.update(fut.result())
                except Exception:  # noqa: BLE001
                    pass
            if cancel_check is not None and cancel_check():
                cancelled = True
                break
    finally:
        if cancelled:
            _terminate_pool(pool)
            pool.shutdown(wait=False, cancel_futures=True)
        else:
            pool.shutdown(wait=True)
    return keep
