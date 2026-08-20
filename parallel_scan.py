"""
多核心并行扫描
==============
CPU 密集：箱体扫描 detect_accumulation_breakout
策略：按股票代码切块 → ProcessPoolExecutor 多进程

Windows 兼容：spawn；worker 为模块顶层函数，可 pickle。
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Any

import numpy as np
import pandas as pd

# 单进程兜底阈值：Windows spawn 有固定开销，票太少时多进程更慢
_MIN_CODES_FOR_POOL = 200

_WATCHDOG_STARTED = False


def _parent_alive(ppid: int) -> bool:
    """跨平台父进程存活探测（安全版）。

    绝不裸用 os.kill(ppid, 0)：Windows 上该调用语义依赖实现版本，
    旧版 CPython 曾将 sig=0 实现为 TerminateProcess(0) 直接杀死父进程；
    且对受保护进程会抛 PermissionError，若一律当"父死"处理会把活着的 worker 自杀。

    只把确定性的"进程不存在"视为父死，其余一律保守判定存活。
    """
    if ppid <= 0:
        return False
    try:
        import psutil

        try:
            p = psutil.Process(ppid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except Exception:  # noqa: BLE001  # AccessDenied/ZombieProcess/其它 → 保守存活
            return True
    except ImportError:
        pass

    if os.name == "nt":
        # Windows 兜底：OpenProcess + GetExitCodeProcess 探测，完全不触碰 TerminateProcess
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(ppid))
        if not h:
            # 87=参数错误（进程不存在）1168=未找到 → 父死；其余（如权限不足）保守存活
            return ctypes.get_last_error() not in (87, 1168)
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(h)

    # POSIX
    try:
        os.kill(ppid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:  # 保守存活，避免误杀 worker
        return True


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
            if not _parent_alive(ppid):
                os._exit(0)  # 父进程已死：立即退出（worker 无共享状态，无需清理）
            time.sleep(interval)

    threading.Thread(target=_watch, daemon=True).start()


def resolve_workers(workers: int | None = None) -> int:
    """workers<=0 或 None → 自动：cpu_count-1（至少 1，最多 8，稳健优先）。"""
    from scan_runtime import safe_workers

    return safe_workers(workers, hard_cap=8)


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
    from scan_runtime import terminate_pool_processes

    terminate_pool_processes(pool)


def _abandon_pool(pool: ProcessPoolExecutor) -> None:
    from scan_runtime import abandon_pool

    abandon_pool(pool)


def _cancelled(cancel_check: Callable[[], bool] | None) -> bool:
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception:  # noqa: BLE001
        return False


def detect_many(
    codes: Iterable[str],
    daily: pd.DataFrame,
    *,
    kwargs: dict[str, Any] | None = None,
    workers: int | None = None,
    progress_cb: Callable[[str, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    label: str = "信号检测",
    min_codes_for_pool: int = _MIN_CODES_FOR_POOL,
) -> dict[str, dict]:
    """对多只股票并行检测，返回 {ts_code: sig}。

    workers=1 或代码数很少时走单进程（便于调试/小样本）。
    cancel_check：返回 True 时停止派发并释放进程池（当前分片跑完后退出）。
    min_codes_for_pool：小于该数量走单进程；Web 总览小样本（如 30 只）
    可传 1 强制多进程，避免 30 只串行重算信号拖慢响应。
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

    if n_workers == 1 or len(code_list) < min_codes_for_pool:
        _prog(f"单进程扫描 {len(code_list)} 只…", 25)
        # 分批可取消，避免整表卡死
        results: dict[str, dict] = {}
        batch = 80
        for i in range(0, len(code_list), batch):
            if _cancelled(cancel_check):
                _prog("已取消（单进程）", 100)
                return results
            part = code_list[i: i + batch]
            pairs = _worker_detect_chunk((part, sub[sub["ts_code"].isin(part)], kwargs))
            for c, s in pairs:
                results[c] = s
            pct = 25 + int(70 * min(i + batch, len(code_list)) / max(len(code_list), 1))
            _prog(f"单进程 {min(i + batch, len(code_list))}/{len(code_list)}", pct)
        return results

    if _cancelled(cancel_check):
        _prog("已取消", 100)
        return {}

    chunks = _chunk_list(code_list, n_workers)
    # 每个 worker 只拿自己的子集，避免全表复制 × N
    tasks: list[tuple[list[str], pd.DataFrame, dict[str, Any]]] = []
    for ch in chunks:
        if _cancelled(cancel_check):
            _prog("已取消（准备分片）", 100)
            return {}
        ch_set = set(ch)
        cdf = sub[sub["ts_code"].isin(ch_set)]
        tasks.append((ch, cdf, kwargs))

    _prog(f"多进程×{len(tasks)} 扫描 {len(code_list)} 只…", 25)
    results = {}
    cancelled = False
    pool = ProcessPoolExecutor(max_workers=len(tasks))
    futs = {}
    try:
        for i, t in enumerate(tasks):
            if _cancelled(cancel_check):
                cancelled = True
                _prog("已请求取消，停止派发…", 90)
                break
            futs[pool.submit(_worker_detect_chunk, t)] = i
        pending = set(futs)
        done = 0
        # wait(timeout=0.8) 轮询：更频繁响应取消
        while pending and not cancelled:
            if _cancelled(cancel_check):
                cancelled = True
                _prog("已请求取消，正在终止进程…", 90)
                break
            finished, pending = wait(pending, timeout=0.8, return_when=FIRST_COMPLETED)
            for fut in finished:
                chunk_codes = tasks[futs[fut]][0] if fut in futs else []
                try:
                    pairs = fut.result(timeout=0.1)
                except Exception as exc:  # noqa: BLE001  # worker 异常 → 分片结果缺失，必须可见
                    pairs = []
                    _prog(
                        f"警告：分片异常（{exc.__class__.__name__}），"
                        f"该分片 {len(chunk_codes)} 只股票未返回结果（静默漏票风险）",
                        pct=0,
                    )
                for c, s in pairs:
                    results[c] = s
                done += 1
                pct = 25 + int(70 * done / max(len(tasks), 1))
                _prog(f"完成分片 {done}/{len(tasks)}（累计 {len(results)} 只）", pct)
            if not pending and not cancelled:
                missing = sorted(set(code_list) - set(results))
                if missing:
                    _prog(
                        f"警告：对账发现 {len(missing)}/{len(code_list)} 只股票未出现在扫描结果"
                        f"（分片失败/无数据）: {missing[:8]}{'…' if len(missing) > 8 else ''}",
                        pct=96,
                    )
    finally:
        if cancelled or _cancelled(cancel_check):
            cancelled = True
            _abandon_pool(pool)
        else:
            try:
                pool.shutdown(wait=True)
            except Exception:  # noqa: BLE001
                _abandon_pool(pool)

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
    progress_cb: Callable[[str, int, str], None] | None = None,
) -> set[str]:
    """并行量能/近高点预筛。支持 cancel_check：取消后立即终止进程池并返回。"""
    code_list = [str(c) for c in codes]
    if not code_list or daily is None or daily.empty:
        return set(code_list)

    def _prog(msg: str, pct: int = 18) -> None:
        if progress_cb:
            try:
                progress_cb("预筛", pct, msg)
            except Exception:  # noqa: BLE001
                pass

    if _cancelled(cancel_check):
        _prog("已取消", 18)
        return set()

    n_workers = resolve_workers(workers)
    sub = daily[daily["ts_code"].isin(set(code_list))]
    if sub.empty:
        return set()

    # 单进程：分批跑，每批检查取消（全表 groupby 一次会卡住很久且无法中断）
    if n_workers == 1 or len(code_list) < _MIN_CODES_FOR_POOL:
        from prefilter_fast import volume_breakout_candidates

        keep: set[str] = set()
        batch = 150
        total = len(code_list)
        for i in range(0, total, batch):
            if _cancelled(cancel_check):
                _prog("已取消（单进程预筛）", 18)
                return set()  # 取消则丢弃部分结果，让上层立刻退出
            part = code_list[i: i + batch]
            keep |= volume_breakout_candidates(
                sub, part, lookback=lookback, vol_ratio_min=vol_ratio_min, near_high_pct=near_high_pct
            )
            _prog(f"预筛 {min(i + batch, total)}/{total}", 18)
        return keep

    if _cancelled(cancel_check):
        return set()

    chunks = _chunk_list(code_list, n_workers)
    tasks = []
    for ch in chunks:
        if _cancelled(cancel_check):
            _prog("已取消（准备预筛分片）", 18)
            return set()
        cdf = sub[sub["ts_code"].isin(set(ch))]
        tasks.append((ch, cdf, lookback, vol_ratio_min, near_high_pct))

    keep = set()
    cancelled = False
    pool = ProcessPoolExecutor(max_workers=max(1, len(tasks)))
    futs = []
    try:
        for t in tasks:
            if _cancelled(cancel_check):
                cancelled = True
                _prog("已请求取消，停止派发预筛…", 18)
                break
            futs.append(pool.submit(_worker_prefilter_chunk, t))
        pending = set(futs)
        done = 0
        while pending and not cancelled:
            if _cancelled(cancel_check):
                cancelled = True
                _prog("已请求取消，正在终止预筛进程…", 18)
                break
            finished, pending = wait(pending, timeout=0.8, return_when=FIRST_COMPLETED)
            for fut in finished:
                chunk_codes = tasks[futs.index(fut)][0] if fut in futs else []
                try:
                    keep.update(fut.result(timeout=0.1))
                except Exception as exc:  # noqa: BLE001
                    _prog(
                        f"警告：预筛分片异常（{exc.__class__.__name__}），"
                        f"该分片 {len(chunk_codes)} 只股票未参与预筛（保守起见按保留处理由上层补扫）",
                        pct=18,
                    )
                    keep.update(set(chunk_codes))
                done += 1
                _prog(f"预筛分片 {done}/{len(futs)}", 18)
    finally:
        if cancelled or _cancelled(cancel_check):
            cancelled = True
            _abandon_pool(pool)
        else:
            try:
                pool.shutdown(wait=True)
            except Exception:  # noqa: BLE001
                _abandon_pool(pool)

    if cancelled:
        _prog("预筛已取消", 18)
        return set()  # 上层 _stop_if_cancelled 立刻 return
    return keep
