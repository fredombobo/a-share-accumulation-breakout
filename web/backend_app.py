"""
横盘吸筹→启动 选股系统 后端 API（SQLite 版 + 异步扫描）
=========================================================
数据统一从本地 SQLite 读取（local_store），不再依赖 xlsx/pkl 扫描产物：
  GET /api/overview          → 最近一次扫描结果（从 scan_result 表读，含 K线+箱体+财报）
  GET /api/stock/{ts_code}   → 个股详情（K线/信号/资金流/基本面/财报）
  POST /api/scan             → 触发异步扫描，立即返回 {task_id}
  GET  /api/scan/status      → 查询最近/指定任务进度（含取消）
  POST /api/scan/{task_id}/cancel → 取消扫描
  GET  /api/sector-flow      → 板块资金流总览
  GET  /api/stock/{ts_code}/flow → 个股+板块资金流趋势
  GET  /api/health

启动：uvicorn backend_app:app --port 8000
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

_BASE = Path(__file__).resolve().parent
_PARENT = _BASE.parent
for _p in (str(_BASE), str(_PARENT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from local_store import LocalStore, sync_fina_for_codes  # noqa: E402
from scoring import (  # noqa: E402
    calc_fund_flow_strength,
    fundamental_filter_passes,
    is_delisted_name,
    is_st_name,
)
from signals import detect_accumulation_breakout  # noqa: E402

from build_version import build_version as _compute_build_version  # noqa: E402

# 后端构建版本与启动时间：启动器据此检测「源码或前端产物更新」并自动重启
_BUILD_VERSION = _compute_build_version()
_STARTED_AT = datetime.now().isoformat(timespec="seconds")

app = FastAPI(title="A股 横盘吸筹→启动 选股系统", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 模块级单例（schema 初始化只做一次） ──
_store = LocalStore()
_SECTOR_FLOW_CACHE: dict = {}  # {(days, data_version): (dates, pivot_df)}
_SIG_CACHE: dict = {}          # {(ts_code, as_of): sig} 个股信号缓存，避免每次 overview 重算

# 阶段0：overview 轻量列表缓存（按数据日期失效），避免每次请求重复全表查询
_OVERVIEW_CACHE: dict = {"key": None, "payload": None}   # key=(as_of, pool)
_SCAN_RESULT_CACHE: dict = {"key": None, "df": None}     # key=max(trade_date)
_DATES_CACHE: dict = {"key": None, "dates": None}        # key=max(trade_date) 全量日期

# ── 异步扫描任务管理 ──
_SCAN_TASKS: dict[str, dict] = {}
_SCAN_CANCEL_EVENTS: dict[str, threading.Event] = {}
_SCAN_LOCK = threading.Lock()
_SCAN_TASKS_MAX = 20          # 历史任务保留上限，防止字典无限增长
_SECTOR_FLOW_CACHE_MAX = 6    # 板块资金流缓存条目上限


def _new_task(top: int, days: int) -> str:
    task_id = uuid.uuid4().hex[:12]
    with _SCAN_LOCK:
        _SCAN_TASKS[task_id] = {
            "id": task_id,
            "top": top,
            "days": days,
            "status": "pending",
            "stage": "排队中",
            "progress": 0,
            "started_at": None,
            "finished_at": None,
            "cancel_requested": False,
            "result": None,
            "error": None,
            "log": [],
        }
        _SCAN_CANCEL_EVENTS[task_id] = threading.Event()
    return task_id


def _running_task_id() -> str | None:
    """当前是否有排队/运行/取消中的扫描；有则返回其 task_id。"""
    with _SCAN_LOCK:
        for tid, t in _SCAN_TASKS.items():
            # cancelling：取消请求已发出但工作线程尚未退出，仍互斥
            if t.get("status") in ("pending", "running", "cancelling"):
                return tid
    return None


def _prune_scan_tasks() -> None:
    """清理已完成任务，仅保留最近 _SCAN_TASKS_MAX 条（防字典无限增长）。"""
    with _SCAN_LOCK:
        if len(_SCAN_TASKS) <= _SCAN_TASKS_MAX:
            return
        overflow = len(_SCAN_TASKS) - _SCAN_TASKS_MAX
        # 优先删最旧的已完成任务（done/error/cancelled）
        done = sorted(
            (tid for tid, t in _SCAN_TASKS.items() if t.get("status") in ("done", "error", "cancelled")),
            key=lambda x: str(_SCAN_TASKS[x].get("finished_at") or ""),
        )
        for tid in done[:overflow]:
            _SCAN_TASKS.pop(tid, None)
            _SCAN_CANCEL_EVENTS.pop(tid, None)


def _log(task: dict, msg: str) -> None:
    task["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    if len(task["log"]) > 200:
        task["log"] = task["log"][-200:]


def _run_scan_worker(task_id: str, top: int, days: int) -> None:
    """后台线程：拉起「可杀」扫描子进程，轮询进度；取消时 taskkill 整树。

    关键：旧实现在同进程内跑 run_scan，阻塞在预筛/排序时线程无法响应取消。
    现改为独立进程 + 进度文件，cancel 可强制结束整棵进程树。
    """
    import json
    import subprocess
    import time as _time

    from scan_runtime import (
        cancel_flag_check,
        clamp_progress,
        force_terminal,
        is_terminal,
        kill_process_tree,
    )

    with _SCAN_LOCK:
        task = _SCAN_TASKS.get(task_id)
        cancel_ev = _SCAN_CANCEL_EVENTS.get(task_id)
    if task is None:
        return
    if cancel_ev is None:
        cancel_ev = threading.Event()
        with _SCAN_LOCK:
            _SCAN_CANCEL_EVENTS[task_id] = cancel_ev

    runtime_dir = _PARENT / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    progress_path = runtime_dir / f"scan_{task_id}.progress.json"
    result_path = runtime_dir / f"scan_{task_id}.result.json"
    cancel_path = runtime_dir / f"scan_{task_id}.cancel"
    for p in (progress_path, result_path, cancel_path):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass

    def cancel_requested() -> bool:
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
        return cancel_flag_check(t, cancel_ev)

    def _mark_cancelled(stage: str = "已取消", msg: str = "扫描已取消") -> None:
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t is None:
                return
            force_terminal(t, "cancelled", stage=stage, log_fn=_log, msg=msg)
            t["cancel_requested"] = True
            t["worker_pid"] = None
        _prune_scan_tasks()

    def report(stage: str, progress: int, msg: str = "") -> None:
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t is None or is_terminal(t.get("status")):
                return
            if t.get("cancel_requested") or t.get("status") == "cancelling":
                t["status"] = "cancelling"
                base = stage or t.get("stage") or ""
                t["stage"] = base if "取消" in str(base) else (f"取消中…{base}" if base else "取消中…")
            else:
                t["status"] = "running"
                t["stage"] = stage
            t["progress"] = clamp_progress(progress)
            if msg:
                _log(t, msg)

    def _kill_job(proc: subprocess.Popen | None) -> None:
        try:
            cancel_path.write_text("1", encoding="utf-8")
        except OSError:
            pass
        pid = None
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t:
                pid = t.get("worker_pid")
        if pid:
            kill_process_tree(int(pid))
        if proc is not None and proc.poll() is None:
            try:
                kill_process_tree(proc.pid)
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    proc: subprocess.Popen | None = None
    try:
        with _SCAN_LOCK:
            task["started_at"] = datetime.now().isoformat()
        report("数据准备", 2, f"启动子进程扫描 top={top} days={days}")

        if cancel_requested():
            _mark_cancelled(msg="启动前已取消")
            return

        runner = _PARENT / "scan_job_runner.py"
        cmd = [
            sys.executable,
            str(runner),
            "--task-id", task_id,
            "--top", str(top),
            "--days", str(days),
            "--progress", str(progress_path),
            "--result", str(result_path),
            "--cancel-file", str(cancel_path),
        ]
        # CREATE_NEW_PROCESS_GROUP 便于 Windows 整树结束
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        proc = subprocess.Popen(
            cmd,
            cwd=str(_PARENT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t:
                t["worker_pid"] = proc.pid
                _log(t, f"scan subprocess pid={proc.pid}")

        # 轮询：进度文件 + 取消 + 子进程退出
        while True:
            if cancel_requested():
                pct = 0
                try:
                    if progress_path.exists():
                        pct = int(json.loads(progress_path.read_text(encoding="utf-8")).get("progress") or 0)
                except Exception:  # noqa: BLE001
                    pct = 0
                report("取消中", clamp_progress(pct), "正在终止扫描进程树…")
                _kill_job(proc)
                try:
                    if proc:
                        proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    _kill_job(proc)
                _mark_cancelled(msg="已终止扫描子进程")
                return

            rc = proc.poll() if proc else 0
            if progress_path.exists():
                try:
                    prog = json.loads(progress_path.read_text(encoding="utf-8"))
                    report(
                        str(prog.get("stage") or "运行中"),
                        int(prog.get("progress") or 0),
                        str(prog.get("message") or ""),
                    )
                except Exception:  # noqa: BLE001
                    pass

            if rc is not None:
                break
            _time.sleep(0.35)

        # 子进程已退出
        if cancel_requested():
            _mark_cancelled(msg="子进程退出时检测到取消")
            return

        result: dict = {}
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                result = {"status": "error", "error": f"result parse: {e}"}

        if result.get("cancelled") or result.get("status") == "cancelled":
            _mark_cancelled(msg="子进程报告已取消")
            return

        if result.get("status") == "error" or (proc and proc.returncode not in (0, None) and not result):
            with _SCAN_LOCK:
                t = _SCAN_TASKS.get(task_id)
                if t:
                    force_terminal(
                        t, "error", stage="失败",
                        error=str(result.get("error") or f"exit={proc.returncode if proc else '?'}"),
                        log_fn=_log, msg="scan subprocess error",
                    )
            return

        count_a = int(result.get("count_a") or result.get("count") or 0)
        count_b = int(result.get("count_b") or 0)
        report(
            "完成", 100,
            f"A={count_a} B={count_b} 环境={(result.get('regime') or {}).get('label')} "
            f"{result.get('elapsed_sec')}s",
        )
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t is None:
                return
            if t.get("cancel_requested"):
                force_terminal(t, "cancelled", stage="已取消", log_fn=_log, msg="完成写入前取消")
                return
            t["status"] = "done"
            t["progress"] = 100
            t["stage"] = "完成"
            t["finished_at"] = datetime.now().isoformat()
            t["worker_pid"] = None
            t["result"] = {
                "status": "ok",
                "latest_date": result.get("latest_date"),
                "total_candidates": result.get("total_candidates", 0),
                "hits": result.get("hits", 0),
                "count": count_a,
                "count_a": count_a,
                "count_b": count_b,
                "regime": result.get("regime"),
                "freshness": result.get("freshness"),
                "pool_report": result.get("pool_report"),
                "elapsed_sec": result.get("elapsed_sec"),
            }
            # 新扫描完成：清除 overview 轻量缓存，避免展示旧数据
            _OVERVIEW_CACHE["key"] = None
            _OVERVIEW_CACHE["payload"] = None

    except Exception as e:  # noqa: BLE001
        _kill_job(proc)
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t is None:
                return
            if t.get("cancel_requested") or cancel_flag_check(t, cancel_ev):
                force_terminal(t, "cancelled", stage="已取消", log_fn=_log, msg=f"异常中取消: {e}")
            else:
                force_terminal(t, "error", stage="失败", error=str(e)[:500], log_fn=_log, msg=str(e)[:200])
    finally:
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t is not None and not is_terminal(t.get("status")):
                if t.get("cancel_requested") or cancel_flag_check(t, cancel_ev):
                    force_terminal(t, "cancelled", stage="已取消", log_fn=_log, msg="finally 收口 cancelled")
                else:
                    force_terminal(
                        t, "error", stage="异常退出",
                        error="worker exited without terminal status",
                        log_fn=_log, msg="finally 收口 error",
                    )
            if t is not None:
                t["worker_pid"] = None
        _prune_scan_tasks()

# ── 数据读取（SQLite） ──

def _kline_series_for(code: str, limit: int | None = None, start: str | None = None) -> list[dict]:
    # SQL 层直接取最近 limit 个交易日，避免全量 K 线拖慢总览。
    # start 由调用方预计算（distinct_dates 全表扫描较贵，不应在循环内重复调用）。
    if limit and limit > 0:
        df = _store.load_daily(ts_codes=[code], start=start) if start else _store.load_daily(ts_codes=[code])
    else:
        df = _store.load_daily(ts_codes=[code])
    if df.empty:
        return []
    df = df.sort_values("trade_date")
    if limit and limit > 0 and len(df) > limit:
        df = df.tail(limit)
    out = []
    for _, r in df.iterrows():
        out.append({
            "trade_date": str(r["trade_date"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "vol": float(r["vol"]),
            "amount": float(r["amount"]) if pd.notna(r.get("amount")) else None,
        })
    return out


def _sig_for(code: str) -> dict:
    """个股信号（带缓存）：每次 overview 对每只重算 detect_accumulation_breakout 很贵，
    以 (code, 最新交易日) 为键缓存；新扫描/新数据后日期变化自动失效。"""
    as_of = _store.max_trade_date("daily") or ""
    key = (code, as_of)
    cached = _SIG_CACHE.get(key)
    if cached is not None:
        return cached
    df = _store.load_daily(ts_codes=[code])
    if df.empty:
        return {}
    df = df.sort_values("trade_date").copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    sig = detect_accumulation_breakout(df)
    _SIG_CACHE[key] = sig
    # 缓存上限：防止内存无限增长（一只约数 KB）
    while len(_SIG_CACHE) > 300:
        _SIG_CACHE.pop(next(iter(_SIG_CACHE)))
    return sig


def _sig_for_many(codes: list[str]) -> dict[str, dict]:
    """批量信号检测（进程池并行，冷请求 30 只从 ~6s 降到 ~1s）。

    未命中缓存的小样本也强制多进程（min_codes_for_pool=1），
    命中缓存的不重算；结果写回 _SIG_CACHE 供后续复用。
    数量很少（<5）时用串行单只计算——spawn 进程池的开销远大于直接算。
    """
    if not codes:
        return {}
    from parallel_scan import detect_many

    as_of = _store.max_trade_date("daily") or ""
    out: dict[str, dict] = {}
    todo: list[str] = []
    for c in codes:
        key = (c, as_of)
        if key in _SIG_CACHE:
            out[c] = _SIG_CACHE[key]
        else:
            todo.append(c)
    if todo:
        if len(todo) < 5:
            # 少量缺失：串行单只计算，避免 spawn 进程池（~3.5s 开销）
            for c in todo:
                try:
                    df = _store.load_daily(ts_codes=[c])
                    if df.empty:
                        sig: dict = {}
                    else:
                        df = df.sort_values("trade_date").copy()
                        df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
                        sig = detect_accumulation_breakout(df)
                except Exception:  # noqa: BLE001
                    sig = {}
                _SIG_CACHE[(c, as_of)] = sig
                out[c] = sig
        else:
            daily = _store.load_daily(ts_codes=todo)
            if not daily.empty:
                sigs = detect_many(todo, daily, workers=None, min_codes_for_pool=1, label="总览信号")
                for c in todo:
                    sig = sigs.get(c) or {}
                    _SIG_CACHE[(c, as_of)] = sig
                    out[c] = sig
        while len(_SIG_CACHE) > 400:
            _SIG_CACHE.pop(next(iter(_SIG_CACHE)))
    return out


def _fina_for(code: str, limit: int = 4) -> list[dict]:
    df = _store.load_fina_indicator(ts_codes=[code])
    if df.empty:
        return []
    df = df.sort_values("ann_date", ascending=False).head(limit)
    out = []
    for _, r in df.iterrows():
        out.append({
            "ann_date": str(r["ann_date"]),
            "end_date": str(r["end_date"]),
            "roe": float(r["roe"]) if pd.notna(r.get("roe")) else None,
            "roe_waa": float(r["roe_waa"]) if pd.notna(r.get("roe_waa")) else None,
            "roa": float(r["roa"]) if pd.notna(r.get("roa")) else None,
            "grossprofit_margin": float(r["grossprofit_margin"]) if pd.notna(r.get("grossprofit_margin")) else None,
            "netprofit_margin": float(r["netprofit_margin"]) if pd.notna(r.get("netprofit_margin")) else None,
            "or_yoy": float(r["or_yoy"]) if pd.notna(r.get("or_yoy")) else None,
            "netprofit_yoy": float(r["netprofit_yoy"]) if pd.notna(r.get("netprofit_yoy")) else None,
            "debt_to_assets": float(r["debt_to_assets"]) if pd.notna(r.get("debt_to_assets")) else None,
            "current_ratio": float(r["current_ratio"]) if pd.notna(r.get("current_ratio")) else None,
            "quick_ratio": float(r["quick_ratio"]) if pd.notna(r.get("quick_ratio")) else None,
            "ocf_to_or": float(r["ocf_to_or"]) if pd.notna(r.get("ocf_to_or")) else None,
            "eps": float(r["eps"]) if pd.notna(r.get("eps")) else None,
            "bps": float(r["bps"]) if pd.notna(r.get("bps")) else None,
        })
    return out


def _load_sector_flow(days: int = 10, force: bool = False) -> tuple[list[str], pd.DataFrame]:
    """按行业聚合的全市场资金流 pivot（行=日期，列=行业，值=净流入万元）。

    直接从本地 SQLite 读取 moneyflow + stock_basic（无需实时拉取）。
    返回 (dates, pivot_df)。
    """
    store = _store
    basic = store.load_stock_basic()
    if basic.empty:
        raise HTTPException(status_code=404, detail="本地库无股票数据，请先运行 sync_daily.py")

    mf_dates = store.distinct_dates("moneyflow", limit=days + 5)
    if not mf_dates:
        raise HTTPException(status_code=500, detail="本地库无资金流数据，请先运行 sync_daily.py")
    hit_dates = mf_dates[-days:]
    data_version = store.max_trade_date("moneyflow")
    cache_key = (days, data_version)
    if not force and cache_key in _SECTOR_FLOW_CACHE:
        return _SECTOR_FLOW_CACHE[cache_key]

    mf = store.load_moneyflow(start=hit_dates[0], end=hit_dates[-1])
    if mf.empty:
        raise HTTPException(status_code=500, detail="本地库无资金流数据，请先运行 sync_daily.py")

    merged = mf.merge(basic[["ts_code", "industry"]], on="ts_code", how="left")
    merged["net"] = pd.to_numeric(merged["net_mf_amount"], errors="coerce").fillna(0)
    grp = merged.groupby(["trade_date", "industry"])["net"].sum().reset_index()
    pivot = grp.pivot(index="trade_date", columns="industry", values="net").fillna(0)
    dates = [str(x) for x in pivot.index.tolist()]
    _SECTOR_FLOW_CACHE[cache_key] = (dates, pivot)
    # 缓存上限：只保留最新 N 条，防止按日期无限增长
    while len(_SECTOR_FLOW_CACHE) > _SECTOR_FLOW_CACHE_MAX:
        _SECTOR_FLOW_CACHE.pop(next(iter(_SECTOR_FLOW_CACHE)))
    return dates, pivot


# ── API ──

@app.get("/api/scan/status")
def scan_status(task_id: str | None = None):
    """查询扫描进度。默认返回最新任务；指定 task_id 返回该任务。"""
    with _SCAN_LOCK:
        if task_id:
            task = _SCAN_TASKS.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
            keys = ("id", "status", "stage", "progress", "cancel_requested", "result", "error", "worker_pid")
            return {k: task.get(k) for k in keys}
        if not _SCAN_TASKS:
            return {"status": "idle", "stage": "无任务", "progress": 0}
        # 返回最新任务
        latest = max(_SCAN_TASKS.values(), key=lambda t: t.get("started_at") or "")
        keys = ("id", "status", "stage", "progress", "cancel_requested", "result", "error", "worker_pid")
        return {k: latest.get(k) for k in keys}


@app.post("/api/scan/{task_id}/cancel")
def cancel_scan(task_id: str):
    from scan_runtime import is_terminal, kill_process_tree, request_cancel, start_cancel_watchdog

    worker_pid = None
    with _SCAN_LOCK:
        task = _SCAN_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
        if is_terminal(task.get("status")):
            return {
                "status": task["status"],
                "stage": task.get("stage"),
                "task_id": task_id,
                "cancel_requested": bool(task.get("cancel_requested")),
            }
        ev = _SCAN_CANCEL_EVENTS.get(task_id)
        if ev is None:
            ev = threading.Event()
            _SCAN_CANCEL_EVENTS[task_id] = ev
        request_cancel(task, ev)
        task["stage"] = "取消中…正在终止扫描进程"
        worker_pid = task.get("worker_pid")
        stage = task["stage"]

    # 立刻写 cancel 文件 + 杀进程树（不等 worker 线程醒来）
    try:
        cancel_file = _PARENT / "runtime" / f"scan_{task_id}.cancel"
        cancel_file.parent.mkdir(parents=True, exist_ok=True)
        cancel_file.write_text("1", encoding="utf-8")
    except OSError:
        pass
    if worker_pid:
        kill_process_tree(int(worker_pid))

    # 看门狗兜底：3s 仍非终态则强制 cancelled
    start_cancel_watchdog(
        task_id=task_id,
        get_task=lambda: _SCAN_TASKS.get(task_id),
        lock=_SCAN_LOCK,
        timeout_sec=3.0,
        prune=_prune_scan_tasks,
        log_fn=_log,
    )
    return {
        "status": "cancelling",
        "stage": stage,
        "task_id": task_id,
        "cancel_requested": True,
        "killed_pid": worker_pid,
    }


class ScanRequest(BaseModel):
    top: int = 20  # A 池默认可交易数量（与箱体阶梯目标一致）
    days: int = 160
    force: bool = False


def _parse_pool_tier(reasons: str) -> tuple[str, str]:
    """从 reasons 前缀解析 池 与 层级。无前缀旧数据标 unknown，避免误入 A。"""
    import re
    s = str(reasons or "")
    m = re.search(r"\[池([AB])\|([^\|\]]+)", s)
    if m:
        return m.group(1), m.group(2).strip()
    if "theme_fill" in s or "主题强制" in s:
        return "B", "theme_fill"
    if "relaxed" in s or "放宽" in s:
        return "B", "relaxed"
    if "[池" in s:
        return "A", "strict"
    # 旧 scan_result 无池前缀：不默认当可交易 A
    return "B", "unknown"


@app.post("/api/scan")
def start_scan(req: ScanRequest):
    """触发异步扫描，立即返回 task_id。

    并发互斥：已有排队/运行中的扫描时返回 409，避免多线程×多进程把 CPU/内存打爆。
    """
    running = _running_task_id()
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"已有扫描正在进行（task_id={running}），请先等待完成或取消后再发起",
        )
    top = max(5, min(req.top, 50))
    days = max(30, min(req.days, 250))
    task_id = _new_task(top, days)
    t = threading.Thread(target=_run_scan_worker, args=(task_id, top, days), daemon=True)
    t.start()
    return {"status": "started", "task_id": task_id, "top": top, "days": days}


@app.get("/api/health")
def health():
    from market_regime import data_freshness, detect_regime
    as_of = _store.max_trade_date("daily") or ""
    # 按交易日历（排除周末/节假日）计算滞后
    fresh = data_freshness(as_of, store=_store)
    try:
        regime = detect_regime(store=_store)
        reg = regime.to_dict()
    except Exception:  # noqa: BLE001
        reg = {"regime": "unknown", "label": "未知"}
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "as_of": as_of,
        "freshness": fresh,
        "regime": reg,
        "build_version": _BUILD_VERSION,
        "started_at": _STARTED_AT,
    }


@app.get("/api/overview")
def overview(pool: str = "A"):
    """最新扫描结果。pool=A|B|ALL。

    无结果时返回 200 + 空列表（不再 404），便于前端保留缓存/提示扫一次。
    """
    from market_regime import data_freshness, detect_regime
    from trade_plan import build_trade_card

    pool = pool.upper()
    as_of_key = _store.max_trade_date("daily") or ""
    # 轻量列表缓存：数据日期 + 池 不变则直接返回（本机热请求 <1s）
    cache_key = (as_of_key, pool)
    if _OVERVIEW_CACHE["key"] == cache_key and _OVERVIEW_CACHE["payload"] is not None:
        return _OVERVIEW_CACHE["payload"]

    df = _store.load_scan_result()
    if df is None or getattr(df, "empty", True):
        as_of = _store.max_trade_date("daily") or ""
        try:
            fresh = data_freshness(as_of, store=_store)
        except Exception:  # noqa: BLE001
            fresh = {"label": "未知", "is_stale": True}
        try:
            regime = detect_regime(store=_store).to_dict()
        except Exception:  # noqa: BLE001
            regime = {"regime": "neutral", "label": "中性"}
        payload = {
            "as_of": as_of,
            "count": 0,
            "pool": pool.upper(),
            "items": [],
            "freshness": fresh,
            "regime": regime,
            "empty_reason": "暂无扫描结果，请先运行扫描",
        }
        _OVERVIEW_CACHE["key"] = cache_key
        _OVERVIEW_CACHE["payload"] = payload
        return payload

    latest = str(df["trade_date"].iloc[0]) if "trade_date" in df.columns else ""
    # 交易日滞后（排除周末/节假日）
    fresh = data_freshness(latest, store=_store)
    try:
        regime = detect_regime(store=_store).to_dict()
    except Exception:  # noqa: BLE001
        regime = {"regime": "neutral", "label": "中性"}

    items = []
    n_a = n_b = 0
    # 第一遍：筛选池 + 统计，收集候选代码
    pool_codes: list[str] = []
    for _, row in df.iterrows():
        code = row["ts_code"]
        reasons = str(row.get("reasons") or "")
        pool_tag, tier = _parse_pool_tier(reasons)
        if pool_tag == "A":
            n_a += 1
        elif pool_tag == "B":
            n_b += 1
        if pool.upper() == "A" and pool_tag != "A":
            continue
        if pool.upper() == "B" and pool_tag != "B":
            continue
        pool_codes.append(code)

    # 信号字段：优先读 scan_result 持久化的 box_high/box_low/ma5/ma20（零计算），
    # 缺失（老数据）才批量并行重算
    sig_map: dict[str, dict] = {}
    need_recalc: list[str] = []
    for _, row in df.iterrows():
        code = row["ts_code"]
        reasons = str(row.get("reasons") or "")
        pool_tag, _tier = _parse_pool_tier(reasons)
        if pool.upper() == "A" and pool_tag != "A":
            continue
        if pool.upper() == "B" and pool_tag != "B":
            continue
        bh = row.get("box_high")
        calculated = row.get("sig_calculated")
        if (bh is not None and pd.notna(bh)) or calculated == 1:
            # 已计算过：直接用持久化字段（box_high 为 NULL 但 sig_calculated=1 是无箱体，合法）
            sig_map[code] = {
                "box_high": float(bh) if bh is not None and pd.notna(bh) else None,
                "box_low": float(row["box_low"]) if pd.notna(row.get("box_low")) else None,
                "ma5": float(row["ma5"]) if pd.notna(row.get("ma5")) else None,
                "ma20": float(row["ma20"]) if pd.notna(row.get("ma20")) else None,
            }
        else:
            need_recalc.append(code)
    if need_recalc:
        sig_map.update(_sig_for_many(need_recalc))

    # 日期窗口只算一次（distinct_dates 全表扫描较贵，避免在循环内重复）
    kline_start = None
    try:
        kline_start = _store.distinct_dates("daily", limit=60)[0]
    except Exception:  # noqa: BLE001
        kline_start = None

    # 批量加载 K 线一次（30 只 × 60 天），按 code 分组复用，避免循环内 30 次串行查库
    kline_by_code: dict[str, list[dict]] = {}
    if pool_codes:  # 空列表时跳过，避免 IN () 退化为全表扫描
        try:
            _kd = _store.load_daily(ts_codes=pool_codes, start=kline_start)
            if not _kd.empty:
                _kd = _kd.sort_values(["ts_code", "trade_date"])
                for _c, _g in _kd.groupby("ts_code", sort=False):
                    _rows = []
                    for _, _r in _g.iterrows():
                        _rows.append({
                            "trade_date": str(_r["trade_date"]),
                            "open": float(_r["open"]),
                            "high": float(_r["high"]),
                            "low": float(_r["low"]),
                            "close": float(_r["close"]),
                            "vol": float(_r["vol"]),
                            "amount": float(_r["amount"]) if pd.notna(_r.get("amount")) else None,
                        })
                    kline_by_code[str(_c)] = _rows
        except Exception:  # noqa: BLE001
            kline_by_code = {}

    # 第二遍：组装轻量条目（不再逐只串行重算信号/加载全量 K 线）
    for _, row in df.iterrows():
        code = row["ts_code"]
        reasons = str(row.get("reasons") or "")
        pool_tag, _tier = _parse_pool_tier(reasons)
        if pool.upper() == "A" and pool_tag != "A":
            continue
        if pool.upper() == "B" and pool_tag != "B":
            continue
        tier = _tier
        sig = sig_map.get(code) or {}
        price = None if pd.isna(row["price"]) else float(row["price"])
        card = build_trade_card(
            price=price,
            box_high=sig.get("box_high"),
            box_low=sig.get("box_low"),
            breakout_date=str(row.get("breakout_date") or ""),
            tier=tier,
            regime=regime.get("regime", "neutral"),
            score=float(row["total_score"]) if pd.notna(row["total_score"]) else None,
        )
        item = {
            "ts_code": code,
            "code": str(code).split(".")[0].zfill(6),
            "name": str(row["name"]),
            "price": price,
            "industry": str(row["industry"]),
            "mv_yi": None if pd.isna(row["mv_yi"]) else float(row["mv_yi"]),
            "pe": None if pd.isna(row["pe"]) else float(row["pe"]),
            "pb": None if pd.isna(row["pb"]) else float(row["pb"]),
            "turnover": None if pd.isna(row["turnover"]) else float(row["turnover"]),
            "score": float(row["total_score"]) if pd.notna(row["total_score"]) else 0,
            "box_days": int(row["box_days"]) if pd.notna(row["box_days"]) else None,
            "box_amp": float(row["box_amp"]) if pd.notna(row["box_amp"]) else None,
            "vol_ratio": float(row["vol_ratio"]) if pd.notna(row["vol_ratio"]) else None,
            "fund_net_wan": float(row["fund_net_wan"]) if pd.notna(row["fund_net_wan"]) else None,
            "fund_ratio": float(row["fund_ratio"]) if pd.notna(row["fund_ratio"]) else None,
            "breakout_date": str(row["breakout_date"]),
            "reasons": reasons,
            "pool": pool_tag,
            "tier": tier,
            "tradeable": card["tradeable"],
            "trade": card,
            # 总览为轻量列表：不返回 fina（财务详情走 /api/stock/{ts_code}），
            # kline 只返回最近 60 条供迷你图，避免 30 个候选全量 K 线 + 财务拖慢响应
            "kline": kline_by_code.get(code) or _kline_series_for(code, limit=60, start=kline_start),
            "box_high": sig.get("box_high"),
            "box_low": sig.get("box_low"),
            "ma5": sig.get("ma5"),
            "ma20": sig.get("ma20"),
        }
        items.append(item)

    # A 池按分数排序
    items.sort(key=lambda x: x.get("score") or 0, reverse=True)
    empty_reason = None
    if not items and (n_a + n_b) > 0:
        if pool.upper() == "A" and n_b > 0:
            empty_reason = f"当前 A 池为空（库内 B 池 {n_b} 只，可切换到 B 或全部）"
        elif pool.upper() == "B" and n_a > 0:
            empty_reason = f"当前 B 池为空（库内 A 池 {n_a} 只，可切换到 A 或全部）"
    payload = {
        "as_of": latest,
        "count": len(items),
        "pool": pool.upper(),
        "freshness": fresh,
        "regime": regime,
        "pool_totals": {"A": n_a, "B": n_b},
        "empty_reason": empty_reason,
        "items": items,
    }
    _OVERVIEW_CACHE["key"] = cache_key
    _OVERVIEW_CACHE["payload"] = payload
    return payload


@app.get("/api/portfolio")
def get_portfolio():
    from portfolio import load_portfolio, check_stops
    data = load_portfolio()
    # 最新价
    prices = {}
    for pos in data.get("positions") or []:
        code = str(pos.get("ts_code", "")).upper()
        d = _store.load_daily(ts_codes=[code])
        if d is not None and not d.empty:
            d = d.sort_values("trade_date")
            prices[code] = float(pd.to_numeric(d.iloc[-1]["close"], errors="coerce") or 0)
    alerts = check_stops(prices)
    return {"portfolio": data, "alerts": alerts, "prices": prices}


@app.post("/api/portfolio")
def post_portfolio(body: dict):
    from portfolio import upsert_position, remove_position, load_portfolio
    action = body.get("action", "upsert")
    code = body.get("ts_code") or body.get("code")
    if not code:
        raise HTTPException(400, "ts_code required")
    if action == "remove":
        return remove_position(code)
    return upsert_position(
        code,
        name=body.get("name") or "",
        cost=body.get("cost"),
        shares=body.get("shares"),
        stop_loss=body.get("stop_loss"),
        note=body.get("note") or "",
    )


@app.get("/api/stock/{ts_code}")
def stock_detail(ts_code: str):
    """个股详情：K线/信号/资金流/基本面/财报"""
    code = ts_code.upper()
    basic = _store.load_stock_basic()
    row_meta = basic[basic["ts_code"] == code]
    if row_meta.empty:
        raise HTTPException(status_code=404, detail=f"未找到 {code}")

    kline = _kline_series_for(code)
    sig = _sig_for(code)
    fina = _fina_for(code, limit=4)

    # 基本面（最新交易日）
    latest_date = _store.max_trade_date("daily_basic") or ""
    db = _store.load_daily_basic(ts_codes=[code])
    fund_row = db[db["trade_date"] == latest_date] if latest_date and not db.empty else db
    fund_row = fund_row.iloc[0] if not fund_row.empty else None

    # 资金流（近5日）：只取最近 5 个交易日，修复原来累计全部历史的 bug
    mf = _store.load_moneyflow(ts_codes=[code])
    mf_rows = mf if not mf.empty else pd.DataFrame()
    fund_net, fund_score, fund_ratio = calc_fund_flow_strength(mf_rows, days=5)

    meta = row_meta.iloc[0]
    from trade_plan import build_trade_card
    from market_regime import detect_regime
    try:
        reg = detect_regime(store=_store).regime
    except Exception:  # noqa: BLE001
        reg = "neutral"
    close_px = float(fund_row["close"]) if fund_row is not None and pd.notna(fund_row.get("close")) else None
    # 从 scan_result 推断层级
    scan = _store.load_scan_result()
    tier = "strict"
    if not scan.empty:
        hit = scan[scan["ts_code"] == code]
        if not hit.empty:
            _, tier = _parse_pool_tier(str(hit.iloc[0].get("reasons") or ""))
    trade = build_trade_card(
        price=close_px,
        box_high=sig.get("box_high"),
        box_low=sig.get("box_low"),
        breakout_date=sig.get("breakout_date"),
        tier=tier,
        regime=reg,
    )
    return {
        "ts_code": code,
        "name": str(meta.get("name", "")),
        "industry": str(meta.get("industry", "")),
        "area": str(meta.get("area", "")),
        "list_date": str(meta.get("list_date", "")),
        "kline": kline,
        "signal": {
            "box_high": sig.get("box_high"),
            "box_low": sig.get("box_low"),
            "box_days": sig.get("box_days"),
            "box_amp": sig.get("box_amp"),
            "breakout_date": sig.get("breakout_date"),
            "breakout_vol_ratio": sig.get("breakout_vol_ratio"),
            "breakout_pct_chg": sig.get("breakout_pct_chg"),
            "vol_shrink_ratio": sig.get("vol_shrink_ratio"),
            "ma5": sig.get("ma5"),
            "ma10": sig.get("ma10"),
            "ma20": sig.get("ma20"),
            "reasons": sig.get("reasons", []),
        },
        "fundamentals": {
            "pe": float(fund_row["pe"]) if fund_row is not None and pd.notna(fund_row.get("pe")) else None,
            "pb": float(fund_row["pb"]) if fund_row is not None and pd.notna(fund_row.get("pb")) else None,
            "total_mv_wan": float(fund_row["total_mv"]) if fund_row is not None and pd.notna(fund_row.get("total_mv")) else None,
            "circ_mv_wan": float(fund_row["circ_mv"]) if fund_row is not None and pd.notna(fund_row.get("circ_mv")) else None,
            "turnover_rate": float(fund_row["turnover_rate"]) if fund_row is not None and pd.notna(fund_row.get("turnover_rate")) else None,
            "volume_ratio": float(fund_row["volume_ratio"]) if fund_row is not None and pd.notna(fund_row.get("volume_ratio")) else None,
            "close": close_px,
        },
        "fund_flow": {
            "net_wan": round(fund_net, 0),
            "score": fund_score,
            "ratio_pct": round(fund_ratio * 100, 3),
            "days": 5,
        },
        "fina": fina,
        "trade": trade,
        "tier": tier,
        "as_of": latest_date or _store.max_trade_date("daily") or "",
    }


@app.get("/api/sector-flow")
def sector_flow(days: int = 10):
    """板块资金流总览：各行业近 N 日每日主力净流入 + Top 流入/流出排行"""
    days = max(5, min(days, 20))
    dates, pivot = _load_sector_flow(days)
    industries = {str(c): [round(float(v), 0) for v in pivot[c].tolist()] for c in pivot.columns}

    cumsum = pivot.sum(axis=0).sort_values(ascending=False)
    top_in = [{"industry": str(k), "net_wan": round(float(v), 0)} for k, v in cumsum.head(8).items()]
    top_out = [{"industry": str(k), "net_wan": round(float(v), 0)} for k, v in cumsum.tail(8).sort_values().items()]

    return {
        "dates": dates,
        "days": days,
        "industries": industries,
        "top_in": top_in,
        "top_out": top_out,
    }


@app.get("/api/stock/{ts_code}/flow")
def stock_flow(ts_code: str, days: int = 20):
    """个股资金流趋势 + 所在板块资金流趋势（近 N 日，可观察建仓/出逃时段）"""
    code = ts_code.upper()
    days = max(5, min(days, 20))
    basic = _store.load_stock_basic()
    row_meta = basic[basic["ts_code"] == code]
    if row_meta.empty:
        raise HTTPException(status_code=404, detail=f"未找到 {code}")
    industry = str(row_meta.iloc[0].get("industry", ""))

    # 个股资金流（直接从本地库读）
    store = _store
    mf_code = pd.DataFrame()
    try:
        mf_all = store.load_moneyflow(ts_codes=[code])
        mf_code = mf_all if not mf_all.empty else pd.DataFrame()
    except Exception:  # noqa: BLE001
        pass

    # 板块资金流（复用/触发聚合缓存）
    try:
        s_dates, s_pivot = _load_sector_flow(min(days, 20))
        sector_net = [round(float(s_pivot.loc[d, industry]), 0) if industry in s_pivot.columns else 0.0
                      for d in s_dates if d in s_pivot.index]
    except Exception:  # noqa: BLE001
        s_dates, sector_net = [], []

    # 个股资金流：按交易日补齐停牌日（net=0），保证与板块轴长度一致
    flow_rows = []
    if not mf_code.empty:
        mf_code = mf_code.sort_values("trade_date")
        by_date = {str(r["trade_date"]): r for _, r in mf_code.iterrows()}
        axis_dates = s_dates if s_dates else [str(x) for x in mf_code["trade_date"]][-days:]
        for d in axis_dates:
            r = by_date.get(d)
            if r is not None:
                net = float(r.get("net_mf_amount") or 0)
                buy_main = float(r.get("buy_elg_amount") or 0) + float(r.get("buy_lg_amount") or 0)
                sell_main = float(r.get("sell_elg_amount") or 0) + float(r.get("sell_lg_amount") or 0)
                flow_rows.append({
                    "trade_date": str(r["trade_date"]),
                    "net_wan": round(net, 0),
                    "buy_main_wan": round(buy_main, 0),
                    "sell_main_wan": round(sell_main, 0),
                    "buy_elg_wan": round(float(r.get("buy_elg_amount") or 0), 0),
                    "buy_lg_wan": round(float(r.get("buy_lg_amount") or 0), 0),
                })
            else:
                flow_rows.append({
                    "trade_date": d,
                    "net_wan": 0,
                    "buy_main_wan": 0,
                    "sell_main_wan": 0,
                    "buy_elg_wan": 0,
                    "buy_lg_wan": 0,
                })

    return {
        "ts_code": code,
        "name": str(row_meta.iloc[0].get("name", "")),
        "industry": industry,
        "days": days,
        "stock_flow": flow_rows,
        "sector_flow": {"dates": s_dates, "net_wan": sector_net},
        "as_of": _store.max_trade_date("moneyflow") or "",
    }


# ── 小白友好：单端口托管前端 dist（无需再开 npm）──
_DIST = _BASE / "frontend" / "dist"
_HAS_DIST = _DIST.is_dir() and (_DIST / "index.html").is_file()


@app.get("/api/setup-status")
def setup_status():
    """新手向导用：Token / 数据 / 扫描是否就绪。"""
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    env_path = _PARENT / ".env"
    if not token and env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("TUSHARE_TOKEN=") and not line.startswith("#"):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:  # noqa: BLE001
            pass
    latest_daily = _store.max_trade_date("daily")
    latest_mf = _store.max_trade_date("moneyflow")
    scan_n = 0
    try:
        df_scan = _store.load_scan_result()
        scan_n = 0 if df_scan is None or getattr(df_scan, "empty", True) else len(df_scan)
    except Exception:  # noqa: BLE001
        scan_n = 0
    return {
        "has_token": bool(token) and token not in ("your_token_here", "changeme"),
        "has_frontend_dist": _HAS_DIST,
        "latest_daily": latest_daily,
        "latest_moneyflow": latest_mf,
        "has_market_data": bool(latest_daily),
        "scan_result_rows": scan_n,
        "ui_mode": "single_port" if _HAS_DIST else "dev_split",
        "open_url": "http://127.0.0.1:8000/" if _HAS_DIST else "http://127.0.0.1:3001/",
        "tips": [
            "没有 Token：编辑项目根目录 .env 填入 TUSHARE_TOKEN",
            "没有行情：双击「一键启动.bat」会自动同步，或点界面「扫描」",
            "A 池为空且提示防守：市场弱，系统故意不开新仓，属正常",
        ],
    }


# ── 策略实验室（P6：闭环优化 → 验证 → 擂台赛） ──
_LAB_TASKS: dict[str, dict] = {}
_LAB_LOCK = threading.Lock()
_LAB_TASKS_MAX = 10


class LabOptimizeRequest(BaseModel):
    strategy: str = "A"  # A: 形态入场+标杆量出场 | B: 五步抓主升+标杆量出场
    # 空字符串 = 使用 research_windows 自动窗；也可手填 YYYYMMDD
    is_start: str = ""
    is_end: str = ""
    oos_start: str = ""
    oos_end: str = ""
    max_codes: int = 4500
    step: int = 5
    # grid=网格搜索 | single=单组人工试跑
    mode: str = "grid"
    # 自定义网格（键 → 取值列表）；空则用 config.GRID_BENCH
    grid: dict | None = None
    # 单组参数（mode=single 时必填）
    vol_ratio_min: float | None = None
    strong_reset: int | None = None
    exit_window: int | None = None
    stop_pct: float | None = None


def _lab_running() -> str | None:
    for tid, t in _LAB_TASKS.items():
        if t.get("status") in ("running", "pending", "cancelling"):
            return tid
    return None


def _resolve_lab_windows(req: LabOptimizeRequest) -> dict:
    """解析 Lab 窗口：缺省走数据驱动 plan。"""
    from research_windows import recommend_research_plan

    plan = recommend_research_plan()
    use_auto = not (req.is_start and req.is_end and req.oos_start and req.oos_end)
    if use_auto:
        return {
            "is_start": plan.is_start,
            "is_end": plan.is_end,
            "oos_start": plan.oos_start,
            "oos_end": plan.oos_end,
            "mode": plan.mode,
            "can_claim_edge": plan.can_claim_edge,
            "label": plan.label,
            "notes": plan.notes,
            "n_dates": plan.n_dates,
        }
    return {
        "is_start": req.is_start,
        "is_end": req.is_end,
        "oos_start": req.oos_start,
        "oos_end": req.oos_end,
        "mode": "manual",
        "can_claim_edge": False,
        "label": "手动窗口",
        "notes": ["手动指定窗口，请自行确认无未来函数与覆盖充足"],
        "n_dates": plan.n_dates,
    }


def _run_lab_worker(task_id: str, req: LabOptimizeRequest, windows: dict) -> None:
    from scan_runtime import clamp_progress, force_terminal, is_terminal
    from walkforward import run_is_oos  # 延迟导入避免启动耦合

    def prog(msg: str, pct: int) -> None:
        t = _LAB_TASKS.get(task_id)
        if not t:
            return
        if t.get("status") == "cancelled" or t.get("cancel_requested"):
            raise RuntimeError("用户取消")
        t["progress"] = clamp_progress(pct)
        t["message"] = msg

    try:
        t = _LAB_TASKS.get(task_id)
        if t:
            if t.get("status") == "cancelled" or t.get("cancel_requested"):
                return
            t["status"] = "running"
            t["windows"] = windows
        if windows.get("mode") == "insufficient":
            raise RuntimeError(
                "日线覆盖不足，无法优化。请更新 TUSHARE_TOKEN 后执行 python sync_history.py"
            )
        # 采样上限硬封顶，防 OOM
        max_codes = max(20, min(int(req.max_codes or 200), 4500))
        step = max(1, min(int(req.step or 10), 60))
        single = None
        grid = req.grid
        if (req.mode or "grid").lower() == "single":
            if None in (req.vol_ratio_min, req.strong_reset, req.exit_window, req.stop_pct):
                raise RuntimeError("单组试跑需要填写：量比 / 清零 / 出场窗 / 止损")
            single = {
                "vol_ratio_min": float(req.vol_ratio_min),
                "strong_reset": int(req.strong_reset),
                "exit_window": int(req.exit_window),
                "stop_pct": float(req.stop_pct),
            }
            grid = None
        elif grid:
            allowed = ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
            clean = {}
            for k in allowed:
                vals = grid.get(k) if isinstance(grid, dict) else None
                if isinstance(vals, list) and vals:
                    # 每档最多 8 个值，防组合爆炸
                    clean[k] = list(vals)[:8]
            grid = clean or None
            if grid:
                n = 1
                for v in grid.values():
                    n *= max(1, len(v))
                if n > 120:
                    raise RuntimeError(f"网格组合过多({n}>120)，请减少勾选档位")

        r = run_is_oos(
            strategy=req.strategy,
            step=step,
            max_codes=max_codes,
            top_n=3,
            progress_cb=prog,
            is_start=windows["is_start"],
            is_end=windows["is_end"],
            oos_start=windows["oos_start"],
            oos_end=windows["oos_end"],
            grid=grid,
            single=single,
        )
        t = _LAB_TASKS.get(task_id)
        if t and (t.get("status") == "cancelled" or t.get("cancel_requested")):
            return
        if t:
            is_all = r["is"].to_dict("records") if not r["is"].empty else []
            t["status"] = "done"
            t["progress"] = 100
            t["result"] = {
                "is_top": is_all[:12],
                "is_all": is_all[:40],
                "oos": r["oos"].to_dict("records") if not r["oos"].empty else [],
                "msg": r.get("msg"),
                "run_mode": r.get("mode") or req.mode,
                "research_mode": windows.get("mode"),
                "can_claim_edge": windows.get("can_claim_edge"),
                "params_used": single or grid,
                "windows": {
                    k: windows[k]
                    for k in ("is_start", "is_end", "oos_start", "oos_end", "mode", "label")
                },
            }
            t["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except RuntimeError as e:
        t = _LAB_TASKS.get(task_id)
        if t and ("取消" in str(e) or t.get("cancel_requested")):
            force_terminal(t, "cancelled", stage="已取消", msg=str(e)[:120])
            return
        if t:
            force_terminal(t, "error", stage="失败", error=str(e)[:500], msg=str(e)[:120])
    except Exception as e:  # noqa: BLE001
        t = _LAB_TASKS.get(task_id)
        if t:
            if t.get("cancel_requested"):
                force_terminal(t, "cancelled", stage="已取消", msg=str(e)[:120])
            else:
                force_terminal(t, "error", stage="失败", error=str(e)[:500], msg=str(e)[:120])
    finally:
        t = _LAB_TASKS.get(task_id)
        if t is not None and not is_terminal(t.get("status")):
            if t.get("cancel_requested"):
                force_terminal(t, "cancelled", stage="已取消", msg="lab finally 收口")
            else:
                force_terminal(t, "error", stage="异常退出", error="lab worker non-terminal", msg="lab finally")


@app.get("/api/lab/research-status")
def lab_research_status(probe_token: bool = False):
    """研究就绪：日线深度、推荐窗、是否可声称 edge。默认不探 Token（避免拖慢 UI）。"""
    from research_windows import research_status_dict

    return research_status_dict(probe_token=probe_token)


@app.get("/api/lab/catalog")
def lab_catalog():
    """方案说明 + 默认可调参数 + 网格选项（前端研究台用）。"""
    from config import (
        BENCH_EXIT_WINDOW,
        BENCH_MAX_HOLD_DAYS,
        BENCH_STOP_PCT,
        BENCH_STRONG_RESET,
        BENCH_VOL_RATIO_MIN,
        BOX_MAX_AMP,
        BOX_MAX_DAYS,
        BOX_MIN_DAYS,
        BREAKOUT_VOL_RATIO,
        GRID_BENCH,
        PLAN_B_CHG_MIN,
        PLAN_B_CROSS_LOOKBACK,
        PLAN_B_MIN_BUILD_DAYS,
        PLAN_B_REATTACK_RATIO,
    )
    from optimizer import grid_combos

    param_docs = [
        {
            "key": "vol_ratio_min",
            "name": "建仓量比门槛",
            "unit": "倍",
            "meaning": "当日量 / 近5日均量 ≥ 此值 且收阳，才记为建仓放量日。越高越严，信号更少。",
            "affects": "A/B 共用（B 还影响建仓序列识别档位）",
            "default": BENCH_VOL_RATIO_MIN,
            "options": list(GRID_BENCH["vol_ratio_min"]),
            "range_hint": "1.2 ~ 2.0",
        },
        {
            "key": "strong_reset",
            "name": "强势日清零根数",
            "unit": "根",
            "meaning": "持仓期量<标杆的连续强势日达到此数，出货计数清零（洗盘后可重新计）。",
            "affects": "出场（标杆量）",
            "default": BENCH_STRONG_RESET,
            "options": list(GRID_BENCH["strong_reset"]),
            "range_hint": "2 ~ 5",
        },
        {
            "key": "exit_window",
            "name": "二次出货窗口",
            "unit": "交易日",
            "meaning": "窗口内累计 2 次「量≥标杆」出货预警则清仓；超时未达则按规则重计/强平。",
            "affects": "出场（标杆量）",
            "default": BENCH_EXIT_WINDOW,
            "options": list(GRID_BENCH["exit_window"]),
            "range_hint": "5 ~ 20",
        },
        {
            "key": "stop_pct",
            "name": "兜底止损",
            "unit": "比例",
            "meaning": "相对入场价最大回撤；触及则优先止损，压过标杆量出场。0.07 = -7%。",
            "affects": "出场（风控）",
            "default": BENCH_STOP_PCT,
            "options": list(GRID_BENCH["stop_pct"]),
            "range_hint": "0.04 ~ 0.12",
        },
    ]

    strategies = {
        "A": {
            "id": "A",
            "name": "形态突破 + 标杆量出场",
            "tagline": "横盘吸筹平台 → 放量突破上沿 → 标杆量管出场",
            "entry_title": "入场（固定规则，网格不改）",
            "entry_steps": [
                f"箱体横盘 {BOX_MIN_DAYS}~{BOX_MAX_DAYS} 交易日（约 1~6 个月）",
                f"稳健振幅 ≤ {BOX_MAX_AMP:.0%}，支撑/压力多次触及，拒绝单边通道",
                f"近 5 日收盘有效突破阻力 + 放量 ≥ {BREAKOUT_VOL_RATIO}× 箱体均量",
                "涨幅适中、站稳、均线多头（收盘>MA20 且 MA5>MA20）",
                "位置约束：避免下跌中继低位假平台",
            ],
            "exit_title": "出场（网格可调 · 标杆量四象限）",
            "exit_steps": [
                "锁定标杆量：建仓放量序列内倒数第 2 根放量柱的量能",
                "量<标杆且阳 → 拉升(持有)；量<标杆且阴 → 洗盘(持有)",
                "量≥标杆 → 出货预警；窗口内累计 2 次 → 清仓",
                f"连续 {BENCH_STRONG_RESET} 根强势日可清零出货计数（参数可调）",
                f"止损 {BENCH_STOP_PCT:.0%} / 最长持有 {BENCH_MAX_HOLD_DAYS} 日强平",
            ],
            "fixed_note": "入场形态阈值在 config/signals，实验室网格只扫出场相关参数。",
        },
        "B": {
            "id": "B",
            "name": "五步抓主升 + 标杆量出场",
            "tagline": "金叉定趋势 → 建仓辨强弱 → 破五再进攻 → 同套标杆量出场",
            "entry_title": "入场（方案 B · 部分受量比档影响）",
            "entry_steps": [
                f"近 {PLAN_B_CROSS_LOOKBACK} 日发生过 MA5 上穿 MA10（金叉），且收盘 > MA20",
                f"信号日前存在已终止建仓序列，放量柱 ≥ {PLAN_B_MIN_BUILD_DAYS} 根",
                f"破五：当日量 ≥ 标杆量 × {PLAN_B_REATTACK_RATIO}，涨幅 ≥ {PLAN_B_CHG_MIN:.0%}",
                "建仓量比门槛 vol_ratio_min 参与网格（影响建仓识别松紧）",
            ],
            "exit_title": "出场（与 A 相同标杆量体系，可对照）",
            "exit_steps": [
                "同一套：标杆量锁定 → 四象限持有/出货 → 二次出货清仓",
                "同一套：强势清零 / 止损 / 最长持有强平",
                "便于 A/B 对照：只换入场，出场口径一致",
            ],
            "fixed_note": "B 的入场对 vol_ratio_min 敏感；其余出场参数与 A 同网格。",
        },
    }

    n_default = len(grid_combos("A"))
    return {
        "strategies": strategies,
        "params": param_docs,
        "grid_default": GRID_BENCH,
        "grid_combo_count": n_default,
        "defaults": {
            "vol_ratio_min": BENCH_VOL_RATIO_MIN,
            "strong_reset": BENCH_STRONG_RESET,
            "exit_window": BENCH_EXIT_WINDOW,
            "stop_pct": BENCH_STOP_PCT,
            "max_hold_days": BENCH_MAX_HOLD_DAYS,
        },
        "pipeline": [
            {"id": "is", "name": "样本内 IS", "desc": "只在 IS 窗网格/试跑，按 PF 排序"},
            {"id": "filter", "name": "过滤", "desc": "胜率≥30% 且 最大回撤≤25%（网格模式）"},
            {"id": "oos", "name": "样本外 OOS", "desc": "Top 组合到 OOS 窗一次性验证，不再调参"},
            {"id": "arena", "name": "擂台", "desc": "CLI pipeline_seed 写入 active/candidate"},
        ],
        "disclaimer": "研究辅助，不是投资建议。可调参数只影响历史回放统计，不直接下单。",
    }


@app.post("/api/lab/optimize")
def lab_optimize(req: LabOptimizeRequest):
    """触发异步 IS/OOS 优化，立即返回 task_id。与扫描共享互斥（都是重计算）。"""
    running = _running_task_id()
    if running:
        raise HTTPException(status_code=409, detail=f"已有扫描进行中（{running}），优化任务排队等扫描完成")
    lab_run = _lab_running()
    if lab_run:
        raise HTTPException(status_code=409, detail=f"已有优化任务进行中（{lab_run}）")
    windows = _resolve_lab_windows(req)
    if windows.get("mode") == "insufficient":
        raise HTTPException(
            status_code=400,
            detail="日线覆盖不足，无法启动优化。请更新 Token 后 python sync_history.py",
        )
    if len(_LAB_TASKS) > _LAB_TASKS_MAX:
        for tid in [k for k, v in _LAB_TASKS.items() if v.get("status") in ("done", "error", "cancelled")]:
            _LAB_TASKS.pop(tid, None)
            if len(_LAB_TASKS) <= _LAB_TASKS_MAX:
                break
    task_id = uuid.uuid4().hex[:12]
    _LAB_TASKS[task_id] = {
        "status": "pending",
        "progress": 0,
        "message": f"排队中 · {windows.get('label', '')}",
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": req.strategy,
        "windows": windows,
    }
    threading.Thread(target=_run_lab_worker, args=(task_id, req, windows), daemon=True).start()
    return {
        "status": "started",
        "task_id": task_id,
        "strategy": req.strategy,
        "research_mode": windows.get("mode"),
        "can_claim_edge": windows.get("can_claim_edge"),
        "windows": windows,
    }


@app.get("/api/lab/status")
def lab_status(task_id: str | None = None):
    if task_id:
        t = _LAB_TASKS.get(task_id)
        if not t:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"task_id": task_id, **t}
    if not _LAB_TASKS:
        return {"task_id": None, "status": "idle"}
    latest = max(_LAB_TASKS.values(), key=lambda t: t.get("started_at") or "")
    return {"task_id": next(k for k, v in _LAB_TASKS.items() if v is latest), **latest}


@app.post("/api/lab/{task_id}/cancel")
def lab_cancel(task_id: str):
    """取消正在运行的优化任务。"""
    from scan_runtime import is_terminal, start_cancel_watchdog

    with _LAB_LOCK:
        t = _LAB_TASKS.get(task_id)
        if not t:
            raise HTTPException(status_code=404, detail="任务不存在")
        if is_terminal(t.get("status")):
            return {"status": t["status"], "msg": "任务已结束，无需取消", "task_id": task_id}
        t["cancel_requested"] = True
        t["status"] = "cancelling"
        t["message"] = "取消中…"
    # lab 靠 prog 检查 cancel_requested；卡死时看门狗强制 cancelled
    start_cancel_watchdog(
        task_id=task_id,
        get_task=lambda: _LAB_TASKS.get(task_id),
        lock=_LAB_LOCK,
        timeout_sec=15.0,
        prune=None,
        log_fn=lambda task, msg: task.__setitem__("message", (msg or "")[:200]),
    )
    return {"status": "cancelling", "task_id": task_id, "msg": "取消请求已发送"}


@app.get("/api/lab/leaderboard")
def lab_leaderboard(kind: str = "IS", strategy: str = "A", limit: int = 20):
    """参数排行榜（param_eval 表或最近一次优化结果）。"""
    import pandas as pd
    from local_store import LocalStore

    st = LocalStore()
    ev = st.load_param_eval(eval_kind=kind)
    if ev.empty:
        # 回退：返回最近一次 lab 优化结果
        done = [t for t in _LAB_TASKS.values() if t.get("status") == "done" and t.get("result")]
        if done:
            latest = max(done, key=lambda t: t.get("finished_at") or "")
            rows = latest["result"].get("is_top" if kind == "IS" else "oos") or []
            return {"rows": rows[:limit], "source": "last_run"}
        return {"rows": [], "source": "empty"}
    ev = ev[ev["eval_kind"] == kind].sort_values("profit_factor", ascending=False).head(limit)
    return {"rows": ev.to_dict("records"), "source": "param_eval"}


@app.get("/api/lab/compare")
def lab_compare(ids: str = ""):
    """A/B 方案 + fixed/bench 出场对比。ids 逗号分隔 param_id；空则返回最近 done 任务的 A/B 摘要。"""
    from local_store import LocalStore

    st = LocalStore()
    if ids:
        pid_list = [p for p in ids.split(",") if p]
        rows = []
        for pid in pid_list:
            r = st.load_strategy_params()
            hit = r[r["param_id"] == pid]
            if not hit.empty:
                rows.append(hit.iloc[0].to_dict())
        return {"rows": rows}
    # 汇总最近优化任务的 A/B 最佳组合
    done = [t for t in _LAB_TASKS.values() if t.get("status") == "done" and t.get("result")]
    out = {}
    for strat in ("A", "B"):
        tasks = [t for t in done if t.get("strategy") == strat]
        if tasks:
            latest = max(tasks, key=lambda t: t.get("finished_at") or "")
            is_top = latest["result"].get("is_top") or []
            out[strat] = is_top[0] if is_top else None
    return {"best_by_strategy": out}


@app.get("/api/lab/arena")
def lab_arena():
    """擂台赛状态看板：strategy_params 全量（active/candidate/retired）。"""
    from local_store import LocalStore

    df = LocalStore().load_strategy_params()
    if df.empty:
        return {"rows": [], "weights": {}}
    weights = {}
    act = df[df["status"] == "active"]
    for _, r in act.iterrows():
        if r.get("oos_profit_factor"):
            weights[r["strategy"]] = float(r["oos_profit_factor"])
    return {"rows": df.to_dict("records"), "weights": weights}


if _HAS_DIST:
    assets_dir = _DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def _spa_index():
        return FileResponse(_DIST / "index.html")

    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str):
        # API 已由上方路由处理；其余走静态或 SPA
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="Not Found")
        # 防路径穿越：显式拒绝 .. 与编码变体；解析后必须仍位于 dist 目录内
        if ".." in full_path:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = _DIST / full_path
        try:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(_DIST.resolve()):
                raise HTTPException(status_code=404, detail="Not Found")
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="Not Found")
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn
    print(f"UI: http://127.0.0.1:8000/  (dist={'yes' if _HAS_DIST else 'no-use :3001'})")
    uvicorn.run(app, host="127.0.0.1", port=8000)
