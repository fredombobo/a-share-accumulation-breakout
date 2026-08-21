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

启动：设置 AB_BACKEND_PORT=8001 后运行 python backend_app.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
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

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scoring import (
    calc_fund_flow_strength,
)
from signals import detect_accumulation_breakout

from ab_screener.api.legacy_state import (
    _DB,
    _BUILD_VERSION,
    _STARTED_AT,
    _INSTANCE_ID,
    _LOGGER,
    _store,
    _SECTOR_FLOW_CACHE,
    _SIG_CACHE,
    _OVERVIEW_CACHE,
    _SCAN_RESULT_CACHE,
    _DATES_CACHE,
    _SCAN_TASKS,
    _SCAN_CANCEL_EVENTS,
    _SCAN_LOCK,
    _SCAN_TASKS_MAX,
    _SECTOR_FLOW_CACHE_MAX,
    _LAB_TASKS,
    _LAB_LOCK,
    _LAB_TASKS_MAX,
    _LAB_STORE,
    _SYNC_LOCK,
    _SYNC_STATE,
    _BT_LOCK,
    _BT_TASKS,
    _BT_TASKS_MAX,
)

if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true":
    raise RuntimeError("LIVE_TRADING_ENABLED 必须保持 false；本项目不包含真实下单能力")

# P0.4 契约接线（P8）：启动时只断言 schema 兼容（绝不自动 DDL），
# 未应用迁移/checksum 漂移 → 拒绝启动（fail-closed）。
from ab_screener.data.schema_check import assert_schema_compatible

assert_schema_compatible(_PARENT / "runtime" / "stock_data.db")

app = FastAPI(title="A股 横盘吸筹→启动 选股系统", version="2.0.0")

# P7.1 装配：v2 routers（与 legacy API 并存；重复 path 由 OpenAPI 测试断言为 0）。
# 本文件已自带 legacy /api/scan 路由，故跳过 scan_router 避免重复 Operation ID。
from ab_screener.api.app_factory import include_v2_routers

include_v2_routers(app, include_scan_router=False)
# G2 拆路由：只读杂项（health/setup-status/manifests/today/release-readiness/kline）
from ab_screener.api.routers.legacy_misc import router as legacy_misc_router

app.include_router(legacy_misc_router)
# G2 拆路由：市场数据（overview/portfolio/stock/sector-flow/money-heatmap/stock-flow）
from ab_screener.api.routers.legacy_market import router as legacy_market_router

app.include_router(legacy_market_router)
# 2026-08-16 整改：CORS 从 "*" 收敛为本机白名单（单端口 8001 + 开发前端 3001）。
# 本服务只绑 127.0.0.1，但跨源读写在浏览器内即可完成——放开 "*" 等于让任意网页
# 读取持仓/纸面账户并触发扫描。同源请求不需要 CORS，白名单只为 vite 开发代理服务。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:3001",
        "http://localhost:3001",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mount_logic_router() -> None:
    """挂载 logic_platform 路由（延迟 import 防循环；失败仅告警不影响宿主）。"""
    try:
        from logic_platform.api.routes import router as _logic_router

        app.include_router(_logic_router)
        _LOGGER.info("logic_platform router 已挂载 /api/logic")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("logic_platform router 挂载失败: %s", exc)


_mount_logic_router()


def _paper_enabled() -> bool:
    return os.environ.get("PAPER_TRADING_ENABLED", "true").lower() == "true"


@app.middleware("http")
async def paper_feature_gate(request: Request, call_next):
    if (
        request.url.path.startswith("/api/paper")
        and request.url.path != "/api/paper/gates/status"
        and not _paper_enabled()
    ):
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "PAPER_TRADING_DISABLED",
                                 "message": "纸面交易模块已关闭", "details": {},
                                 "retryable": False}},
        )
    return await call_next(request)


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    """防跨站：Host 须为本机主机名、写操作 Origin 须为本机主机名。

    2026-08-16 整改（对应 CORS "*" 漏洞）：绑定 127.0.0.1 不能阻止用户浏览器里
    的恶意网页向本服务发起请求（CSRF / DNS rebinding）。规则：
      - Host 的主机名必须是 127.0.0.1 / localhost / ::1（端口不限——dev 前端
        或本机其它工具端口都放行，外部域名 rebinding 一律拒绝）；
      - 写方法（POST/PUT/PATCH/DELETE）若带 Origin，其主机名同样必须是本机；
      - 不带 Origin 的写请求（curl / Agent 脚本）放行，保持 CLI 兼容。
    """
    import urllib.parse

    def _hostname_of(raw: str) -> str:
        try:
            host = urllib.parse.urlparse(raw if "//" in raw else f"//{raw}").hostname
            return (host or "").lower()
        except (ValueError, AttributeError):
            return ""

    local_hostnames = {"127.0.0.1", "localhost", "::1"}
    # starlette TestClient 默认 Host=testserver：仅测试放行（攻击者无法注册该域名做 rebinding）
    local_hostnames.add("testserver")

    host = request.headers.get("host") or ""
    if host and _hostname_of(host) not in local_hostnames:
        return JSONResponse(status_code=403, content={"detail": "仅允许本机访问"})

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin and _hostname_of(origin) not in local_hostnames:
            return JSONResponse(
                status_code=403,
                content={"detail": "跨站写请求被拒绝（仅允许本机来源）"},
            )
    return await call_next(request)


# ── 模块级单例（schema 初始化只做一次） ──

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


def _finish_persisted_scan_failure(
    task_id: str,
    error: str,
    *,
    db_path: str | Path | None = None,
) -> bool:
    """Move the durable scan job to FAILED without overriding a terminal state."""
    from ab_screener.application.scan_jobs import finish_persisted_scan_failure

    return finish_persisted_scan_failure(
        task_id, error, db_path=db_path or (_PARENT / "runtime" / "stock_data.db")
    )


def _run_scan_worker(task_id: str, top: int, days: int) -> None:
    """后台线程：拉起「可杀」扫描子进程，轮询进度；取消时 taskkill 整树。

    关键：旧实现在同进程内跑 run_scan，阻塞在预筛/排序时线程无法响应取消。
    现改为独立进程 + 进度文件，cancel 可强制结束整棵进程树。
    """
    import json
    import time as _time

    from ab_screener.application.scan_spawn import ScanChild, spawn_scan_runner
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
        try:
            from ab_screener.application.scan_jobs import CANCELLED, ScanJobStore

            ScanJobStore(_store.db_path).finish(
                task_id,
                status=CANCELLED,
                error_code="CANCELLED",
                error_message=msg,
            )
        except Exception:  # noqa: BLE001
            pass
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

    def _kill_job(proc: ScanChild | None) -> None:
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

    proc: ScanChild | None = None
    try:
        with _SCAN_LOCK:
            task["started_at"] = datetime.now().isoformat()
        report("数据准备", 2, f"启动子进程扫描 top={top} days={days}")

        if cancel_requested():
            _mark_cancelled(msg="启动前已取消")
            return

        proc = spawn_scan_runner(
            task_id=task_id,
            top=top,
            days=days,
            progress=progress_path,
            result=result_path,
            cancel_file=cancel_path,
            cwd=_PARENT,
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
            error_message = str(result.get("error") or f"exit={proc.returncode if proc else '?'}")
            with _SCAN_LOCK:
                t = _SCAN_TASKS.get(task_id)
                if t:
                    force_terminal(
                        t, "error", stage="失败",
                        error=error_message,
                        log_fn=_log, msg="scan subprocess error",
                    )
            _finish_persisted_scan_failure(task_id, error_message, db_path=_store.db_path)
            return

        count_a = int(result.get("count_a") or result.get("count") or 0)
        count_b = int(result.get("count_b") or 0)
        report("固化审计", 99, "正在原子写入扫描结果与运行审计")
        from ab_screener.application.scan_audit import complete_scan_run
        from ab_screener.domain.profile import default_profile
        from research_windows import recommend_research_plan

        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t is None:
                return
            if t.get("cancel_requested"):
                force_terminal(t, "cancelled", stage="已取消", log_fn=_log, msg="完成写入前取消")
                return
            profile = default_profile()
            completed = complete_scan_run(
                _store.db_path,
                run_id=task_id,
                task_id=task_id,
                as_of=str(result.get("latest_date") or ""),
                days=days,
                result=result if isinstance(result, dict) else {},
                count_a=count_a,
                count_b=count_b,
                strategy_snapshot=profile.to_canonical_dict(),
                config_hash=profile.config_hash(),
                code_version=_BUILD_VERSION,
                research_mode=recommend_research_plan().mode,
            )
            if not completed:
                force_terminal(
                    t,
                    "cancelled",
                    stage="已取消",
                    log_fn=_log,
                    msg="扫描审计落库前收到取消请求",
                )
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
        report(
            "完成",
            100,
            f"A={count_a} B={count_b} 环境={(result.get('regime') or {}).get('label')} "
            f"{result.get('elapsed_sec')}s",
        )

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
        try:
            from ab_screener.application.scan_jobs import CANCELLED, FAILED, ScanJobStore

            st = ScanJobStore(_PARENT / "runtime" / "stock_data.db")
            if cancel_requested():
                st.finish(task_id, status=CANCELLED, error_code="CANCELLED")
            else:
                st.finish(task_id, status=FAILED, error_code="ERROR", error_message=str(e)[:500])
        except Exception:  # noqa: BLE001
            pass
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











# ── API ──

@app.get("/api/scan/status")
def scan_status(task_id: str | None = None):
    """查询扫描进度。默认返回最新任务；指定 task_id 返回该任务。"""
    with _SCAN_LOCK:
        if task_id:
            task = _SCAN_TASKS.get(task_id)
            if task is None:
                task = None
            else:
                keys = ("id", "status", "stage", "progress", "cancel_requested", "result", "error", "worker_pid")
                return {k: task.get(k) for k in keys}
        elif _SCAN_TASKS:
            # 返回最新内存任务
            latest = max(_SCAN_TASKS.values(), key=lambda t: t.get("started_at") or "")
            keys = ("id", "status", "stage", "progress", "cancel_requested", "result", "error", "worker_pid")
            return {k: latest.get(k) for k in keys}

    # 服务重启后从持久任务表恢复查询语义。
    from ab_screener.application.scan_jobs import ScanJobStore, to_api_status

    store = ScanJobStore(_store.db_path)
    job = store.get(task_id) if task_id else store.latest()
    if task_id and not job:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return to_api_status(job)


@app.post("/api/scan/{task_id}/cancel")
def cancel_scan(task_id: str):
    from scan_runtime import (
        is_terminal,
        kill_process_tree,
        request_cancel,
        start_cancel_watchdog,
    )

    worker_pid = None
    with _SCAN_LOCK:
        task = _SCAN_TASKS.get(task_id)
        if task is None:
            task = None
        else:
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

    if task is None:
        from ab_screener.application.scan_jobs import (
            CANCELLED,
            QUEUED,
            ScanJobStore,
            to_api_status,
        )

        store = ScanJobStore(_store.db_path)
        persisted = store.get(task_id)
        if not persisted:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
        if persisted.get("status") in ("CANCELLED", "SUCCEEDED", "FAILED"):
            return to_api_status(persisted)
        store.request_cancel(task_id)
        cancel_file = _PARENT / "runtime" / f"scan_{task_id}.cancel"
        cancel_file.parent.mkdir(parents=True, exist_ok=True)
        cancel_file.write_text("1", encoding="utf-8")
        if persisted.get("status") == QUEUED:
            store.finish(task_id, status=CANCELLED, error_code="CANCELLED")
        return to_api_status(store.get(task_id))

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
    # 同步持久任务取消标记
    try:
        from ab_screener.application.scan_jobs import ScanJobStore

        ScanJobStore(_store.db_path).request_cancel(task_id)
    except Exception:  # noqa: BLE001
        pass
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




@app.get("/api/scan/runs")
def list_scan_runs(limit: int = 20):
    """扫描回放列表（upgrade system）。"""
    from ab_screener.data.scan_run_repository import list_scan_runs as _list_runs

    return {"runs": _list_runs(_store.db_path, limit)}


@app.get("/api/scan/runs/{run_id}")
def get_scan_run(run_id: str):
    """单次扫描运行 + 漏斗。"""
    from ab_screener.data.scan_run_repository import ScanRunNotFound, ScanRunSchemaMissing
    from ab_screener.data.scan_run_repository import get_scan_run as _get_run

    try:
        return _get_run(_store.db_path, run_id)
    except ScanRunNotFound:
        raise HTTPException(status_code=404, detail="run not found")
    except ScanRunSchemaMissing as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/scan")
def start_scan(req: ScanRequest):
    """触发异步扫描，立即返回 task_id。

    并发互斥：已有排队/运行中的扫描时返回 409，避免多线程×多进程把 CPU/内存打爆。
    """
    running = _running_task_id()
    if not running:
        try:
            from ab_screener.application.scan_jobs import ScanJobStore

            active = ScanJobStore(_store.db_path).latest_active()
            running = str(active["task_id"]) if active else None
        except Exception:  # noqa: BLE001
            running = None
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"已有扫描正在进行（task_id={running}），请先等待完成或取消后再发起",
        )
    top = max(5, min(req.top, 50))
    days = max(30, min(req.days, 250))
    task_id = _new_task(top, days)
    # upgrade system：持久任务用 upsert_running（禁止 INSERT OR REPLACE 覆盖终态）
    try:
        from ab_screener.application.scan_jobs import ScanJobStore

        ScanJobStore(_store.db_path).upsert_running(task_id, top_n=top, days=days)
    except Exception:  # noqa: BLE001
        pass
    t = threading.Thread(target=_run_scan_worker, args=(task_id, top, days), daemon=True)
    t.start()
    cfg_hash = None
    try:
        from ab_screener.domain.profile import default_profile

        cfg_hash = default_profile().config_hash()
    except Exception:  # noqa: BLE001
        cfg_hash = None
    as_of = _store.max_trade_date("daily")
    return {
        "status": "started",
        "task_id": task_id,
        "top": top,
        "days": days,
        "run_id": task_id,
        "config_hash": cfg_hash,
        "as_of": as_of,
        "dataset_version": as_of,
        "engine_path": "subprocess_v2",  # 子进程扫描 + 持久 job 双写
    }






# ── 纸面交易 API（paper_trading 领域模块）──


def _paper_err(e: Exception) -> None:
    """DomainError → 结构化错误响应 {code, message, details, retryable}（raise）。"""
    from paper_trading.errors import DomainError

    if isinstance(e, DomainError):
        raise HTTPException(status_code=409 if not e.retryable else 429, detail=e.to_dict())
    from tushare_init import sanitize_error
    raise HTTPException(status_code=500, detail={
        "code": "INTERNAL_ERROR", "message": sanitize_error(e)[:300],
        "details": {}, "retryable": False,
    })


def _paper_write(key: str | None, operation: str, payload: dict, callback):
    """所有纸面交易 POST 的统一持久化幂等边界。"""
    from paper_trading.idempotency import execute_idempotent

    return execute_idempotent(_DB, key or "", operation, payload, callback)


@app.get("/api/paper/account")
def paper_account():
    """读取纸面账户（无 → 404）。"""
    from paper_trading.account import get_account

    try:
        return get_account(_DB)
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/account")
def paper_create_account(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """创建唯一纸面账户：{initial_cash_fen: int}。已存在 → 409。"""
    from paper_trading.account import create_account

    try:
        fen = body.get("initial_cash_fen")
        return _paper_write(
            idempotency_key, "paper.account.create", body,
            lambda: create_account(_DB, int(fen) if fen is not None else 0),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.get("/api/paper/dashboard")
def paper_dashboard():
    """账户摘要 + 持仓 + 期初权益 + 风险状态。"""
    from paper_trading.account import get_account, opening_equity
    from paper_trading.errors import ERR_UNKNOWN_ACCOUNT, DomainError

    try:
        from ab_screener.data.paper_query import load_dashboard_extras

        acct = get_account(_DB)
        eq = opening_equity(_DB)
        extras = load_dashboard_extras(_DB)
        return {
            "account": acct,
            "equity": eq,
            "equity_curve": extras["equity_curve"],
            "guide": __import__(
                "paper_trading.guidance", fromlist=["build_guide"]
            ).build_guide(_DB),
            "risk": {
                "gross_exposure_limit_pct": "80",
                "cash_buffer_pct": "10",
                "daily_buy_limit_pct": "20",
                "single_instrument_limit_pct": "10",
                "reserved_cash_fen": extras["reserved_cash_fen"],
                "reserved_sell_qty": extras["reserved_sell_qty"],
            },
            "unresolved_reconciliation_count": extras["unresolved_reconciliation_count"],
            "paper_notice": "纸面仿真，不会向券商下单",
        }
    except DomainError as e:
        if e.code == ERR_UNKNOWN_ACCOUNT:
            from paper_trading.guidance import build_guide
            return {"account": None, "equity": None, "guide": build_guide(_DB),
                    "paper_notice": "纸面仿真，不会向券商下单"}
        _paper_err(e)
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


def _resolve_import_path(raw: str | None) -> str:
    """纸面导入路径白名单（2026-08-16 整改：修复任意文件读取）。

    仅允许 runtime/portfolio.json；拒绝绝对路径、.. 穿越与其它文件名。
    """
    if not raw:
        return str(_PARENT / "runtime" / "portfolio.json")
    try:
        resolved = Path(raw).resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="path 无效") from exc
    runtime_dir = (_PARENT / "runtime").resolve()
    if not resolved.is_relative_to(runtime_dir) or resolved.name != "portfolio.json":
        raise HTTPException(
            status_code=400,
            detail="仅允许导入 runtime/portfolio.json",
        )
    return str(resolved)


@app.post("/api/paper/import/preview")
def paper_import_preview(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """导入前预览：{path: portfolio.json 路径} → 逐条校验 + 行情。"""
    from paper_trading.account import preview_import

    try:
        path = _resolve_import_path(body.get("path"))
        return _paper_write(
            idempotency_key, "paper.import.preview", body,
            lambda: preview_import(_DB, path),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/import/commit")
def paper_import_commit(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """确认导入：{path, as_of_date?} → 生成 OPENING 批次（幂等）。"""
    from paper_trading.account import commit_import

    try:
        path = _resolve_import_path(body.get("path"))
        return _paper_write(
            idempotency_key, "paper.import.commit", body,
            lambda: commit_import(_DB, path, as_of_date=body.get("as_of_date")),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.get("/api/paper/gates/status")
def paper_gates_status():
    """门禁状态：运行时新鲜度 + 领域表就绪。"""
    from market_regime import data_freshness

    try:
        fresh = data_freshness(_store.max_trade_date("daily") or "", store=_store)
    except Exception:  # noqa: BLE001
        fresh = {"label": "未知", "is_stale": True}
    try:
        from paper_trading.migrations import current_schema_version

        sv = current_schema_version(_DB)
    except Exception:  # noqa: BLE001
        sv = 0
    return {
        "paper_enabled": _paper_enabled(),
        "schema_version": sv,
        "runtime_freshness": fresh,
        "real_data_gate": _latest_gate_status(),
    }


def _latest_gate_status() -> dict:
    from ab_screener.data.paper_query import latest_gate_status

    return latest_gate_status(_DB)


@app.get("/api/paper/orders")
def paper_orders(state: str | None = None, ts_code: str | None = None, limit: int = 50):
    """查询订单：按状态/标的过滤。"""
    from paper_trading.orders import list_orders

    try:
        return {"orders": list_orders(_DB, state=state, ts_code=ts_code, limit=limit)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.get("/api/paper/trading-calendar")
def paper_trading_calendar(start: str, end: str):
    """本地交易日历与账本允许日期边界。"""
    from paper_trading.guidance import trading_calendar

    try:
        return trading_calendar(_DB, start=start, end=end)
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/orders/review")
def paper_review_order(body: dict):
    """只读订单预览；不需要幂等键且不会创建业务记录。"""
    from paper_trading.guidance import review_order

    try:
        return review_order(
            _DB,
            scope=body.get("scope") or "ACCOUNT",
            side=body.get("side") or "BUY",
            mode=body.get("mode") or "MANUAL_HISTORY",
            ts_code=body.get("ts_code") or "",
            qty=int(body.get("qty") or 0),
            execution_trade_date=body.get("execution_trade_date") or "",
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/orders/drafts")
def paper_create_draft(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """创建草稿：{side:'BUY', ts_code, trade_date, suggested_pos_pct?, qty?} 或 {side:'SELL', ts_code, qty}。"""
    from paper_trading.orders import (
        create_buy_draft,
        create_historical_buy_draft,
        create_sell_draft,
    )

    try:
        side = (body.get("side") or "BUY").upper()
        if side == "BUY":
            if str(body.get("mode") or "").upper() == "MANUAL_HISTORY":
                return _paper_write(
                    idempotency_key, "paper.order.draft.buy.historical", body,
                    lambda: create_historical_buy_draft(
                        _DB,
                        ts_code=body["ts_code"],
                        execution_trade_date=body["execution_trade_date"],
                        qty=int(body["qty"]),
                    ),
                )
            return _paper_write(
                idempotency_key, "paper.order.draft.buy", body,
                lambda: create_buy_draft(
                    _DB, ts_code=body["ts_code"], trade_date=body.get("trade_date")
                    or datetime.now().strftime("%Y%m%d"),
                    suggested_pos_pct=body.get("suggested_pos_pct"),
                    input_hash=body.get("input_hash") or "",
                    qty=body.get("qty"),
                ),
            )
        return _paper_write(
            idempotency_key, "paper.order.draft.sell", body,
            lambda: create_sell_draft(_DB, ts_code=body["ts_code"], qty=int(body["qty"])),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/orders/{order_id}/confirm")
def paper_confirm_order(
    order_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """确认订单：预交易检查 + 预留资产。"""
    from paper_trading.orders import confirm_order

    try:
        return _paper_write(
            idempotency_key, "paper.order.confirm", {"order_id": order_id},
            lambda: confirm_order(_DB, order_id),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/orders/{order_id}/cancel")
def paper_cancel_order(
    order_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """取消订单：释放预留。"""
    from paper_trading.orders import cancel_order

    try:
        return _paper_write(
            idempotency_key, "paper.order.cancel", {"order_id": order_id},
            lambda: cancel_order(_DB, order_id),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.get("/api/paper/positions")
def paper_positions():
    """持仓汇总。"""
    from paper_trading.settlement import get_positions

    try:
        return {"positions": get_positions(_DB)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/cycles/run")
def paper_run_cycle(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """手动补跑日结：{trade_date}。幂等（同日期已 DONE 返回原结果）。"""
    from paper_trading.settlement import run_settlement

    try:
        trade_date = body.get("trade_date") or datetime.now().strftime("%Y%m%d")
        return _paper_write(
            idempotency_key, "paper.cycle.run", body,
            lambda: run_settlement(_DB, trade_date),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.get("/api/paper/cycles/{trade_date}")
def paper_cycle_status(trade_date: str):
    """查看日结状态。"""
    from ab_screener.data.paper_query import cycle_status

    try:
        return cycle_status(_DB, trade_date)
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/reconciliation/run")
def paper_run_reconciliation(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """独立重跑对账。"""
    from paper_trading.settlement import run_reconciliation

    try:
        trade_date = body.get("trade_date") or datetime.now().strftime("%Y%m%d")
        return _paper_write(
            idempotency_key, "paper.reconciliation.run", body,
            lambda: run_reconciliation(_DB, trade_date),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.post("/api/paper/corporate-actions/{action_id}/apply")
def paper_apply_corporate_action(
    action_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from paper_trading.settlement import apply_corporate_action

    try:
        payload = {"action_id": action_id}
        return _paper_write(
            idempotency_key, "paper.corporate_action.apply", payload,
            lambda: apply_corporate_action(_DB, action_id),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.get("/api/paper/reconciliation")
def paper_reconciliation(trade_date: str | None = None):
    """查询对账记录及差异。"""
    from ab_screener.data.paper_query import list_reconciliations

    try:
        return {"items": list_reconciliations(_DB, trade_date)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.get("/api/paper/corporate-actions")
def paper_corporate_actions(status: str | None = None, limit: int = 50):
    from ab_screener.data.paper_query import list_corporate_actions

    try:
        return {"items": list_corporate_actions(_DB, status=status, limit=limit)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@app.get("/api/paper/fills")
def paper_fills(limit: int = 50):
    """查询成交记录。"""
    from ab_screener.data.paper_query import list_fills

    try:
        return {"fills": list_fills(_DB, limit=limit)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)










# ── 小白友好：单端口托管前端 dist（无需再开 npm）──
_DIST = _BASE / "frontend" / "dist"
_HAS_DIST = _DIST.is_dir() and (_DIST / "index.html").is_file()


# ── 策略实验室（P6：闭环优化 → 验证 → 擂台赛） ──
def _recover_orphaned_lab_runs(process_name: str | None = None) -> int:
    """Recover only in the web process, never in spawned optimizer workers.

    On Windows a ProcessPool worker imports this module again.  Running recovery
    there would quarantine the parent web process's active Lab row and make the
    parent mistake that interruption for a user cancellation.
    """
    if process_name is None:
        from multiprocessing import current_process

        process_name = current_process().name
    if process_name != "MainProcess":
        return 0
    return _LAB_STORE.mark_orphaned_interrupted()


_recover_orphaned_lab_runs()


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
    force: bool = False


def _lab_running() -> str | None:
    active = _LAB_STORE.latest_active()
    return str(active["research_run_id"]) if active is not None else None


def _select_lab_task(tasks: dict[str, dict]) -> tuple[str, dict] | None:
    """Select the task a returning Lab page should restore.

    An active task always wins over a newer terminal task.  This keeps a route
    remount from hiding work that is still in progress.
    """
    if not tasks:
        return None
    active_states = {"pending", "running", "cancelling"}
    active = [(task_id, task) for task_id, task in tasks.items()
              if task.get("status") in active_states]
    candidates = active or list(tasks.items())
    return max(candidates, key=lambda item: item[1].get("started_at") or "")


def _lab_public_record(record: dict) -> dict:
    raw_request = record.get("request")
    request_data: dict = raw_request if isinstance(raw_request, dict) else {}
    return {
        "task_id": record.get("research_run_id"),
        "research_run_id": record.get("research_run_id"),
        "status": record.get("status") or "idle",
        "phase": record.get("phase"),
        "progress": int(record.get("progress") or 0),
        "message": record.get("message"),
        "error": record.get("message") if record.get("status") == "error" else None,
        "result": record.get("result"),
        "strategy": record.get("strategy"),
        "windows": request_data.get("_windows"),
        "verdict": record.get("verdict"),
        "candidate_eligible": bool(record.get("candidate_eligible")),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
    }


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
            "data_ready_for_edge_validation": plan.data_ready_for_edge_validation,
            "label": plan.label,
            "notes": plan.notes,
            "n_dates": plan.n_dates,
            "wf_windows": plan.to_dict().get("wf_windows", []),
            "automatic_window": True,
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
        "wf_windows": [],
        "automatic_window": False,
    }


def _run_lab_worker(task_id: str, req: LabOptimizeRequest, windows: dict) -> None:
    from ab_screener.research.trusted_run import execute_trusted_research
    from optimizer import ResearchCancelled

    stored = _LAB_STORE.get(task_id) or {}
    request_data = dict(stored.get("request") or {})
    request_data.pop("_windows", None)
    request_data.pop("force", None)
    checkpoint = stored.get("checkpoint") or {}

    def phase(phase_name: str, pct: int, message: str, state: dict) -> None:
        if _LAB_STORE.is_cancel_requested(task_id):
            raise ResearchCancelled("用户取消")
        task = _LAB_TASKS.get(task_id)
        if task is None:
            raise RuntimeError("任务状态丢失")
        task.update({"status": "running", "phase": phase_name, "progress": pct, "message": message})
        _LAB_STORE.update(
            task_id, status="running", phase=phase_name, progress=pct,
            message=message, checkpoint=state,
        )

    try:
        task = _LAB_TASKS.get(task_id)
        if task is None:
            return
        task.update({"status": "running", "windows": windows})
        _LAB_STORE.update(task_id, status="running", phase=stored.get("phase") or "IS", progress=int(stored.get("progress") or 0))
        result = execute_trusted_research(
            research_run_id=task_id,
            request=request_data,
            windows=windows,
            db_path=_store.db_path,
            code_version=str(stored.get("code_version") or _BUILD_VERSION),
            dataset_version=str(stored.get("dataset_version") or "unknown"),
            phase_cb=phase,
            checkpoint=checkpoint,
            cancel_check=lambda: _LAB_STORE.is_cancel_requested(task_id),
        )
        state = result.pop("checkpoint")
        report = result.get("trusted_report") or {}
        frozen = result.get("frozen_candidate") or {}
        primary = frozen.get("is") or {}
        if report.get("candidate_eligible") and primary.get("param_id"):
            _LAB_STORE.add_candidate(
                task_id,
                strategy=str(primary.get("strategy") or req.strategy),
                param_id=str(primary["param_id"]),
                params={
                    key: primary.get(key)
                    for key in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
                },
                metrics={
                    **(frozen.get("oos") or {}),
                    "anti_overfit_version": (report.get("anti_overfit") or {}).get("version"),
                    "gate_verdict": report.get("verdict"),
                    "report_sha256": hashlib.sha256(
                        str(report.get("markdown") or "").encode("utf-8")
                    ).hexdigest(),
                },
            )
        task.update({
            "status": "done", "phase": "CANDIDATE", "progress": 100,
            "message": report.get("summary") or "可信报告已生成", "result": result,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        })
        _LAB_STORE.update(
            task_id,
            status="done",
            phase="CANDIDATE",
            progress=100,
            message=str(task["message"]),
            checkpoint=state,
            result=result,
            is_rows=result.get("is_all") or [],
            oos_rows=result.get("oos") or [],
            baselines=result.get("baselines") or {},
            promotion=result.get("promotion_checks") or {},
            verdict=report.get("verdict"),
            candidate_eligible=bool(report.get("candidate_eligible")),
            can_claim_edge=bool(report.get("candidate_eligible")),
            report_markdown=report.get("markdown") or "",
        )
    except Exception as exc:
        task = _LAB_TASKS.get(task_id)
        persisted_before = _LAB_STORE.get(task_id)
        persisted_cancel = bool(
            persisted_before
            and (
                persisted_before.get("cancel_requested")
                or persisted_before.get("status") == "cancelling"
            )
        )
        runtime_cancel = bool(task and task.get("cancel_requested"))
        cancelled = persisted_cancel or runtime_cancel
        status = "cancelled" if cancelled else "error"
        if cancelled:
            message = "已取消"
        elif isinstance(exc, ResearchCancelled):
            message = f"研究任务意外停止：未收到取消请求；{exc}"[:200]
            _LOGGER.exception("Lab worker stopped without a cancellation request task_id=%s", task_id)
        else:
            message = str(exc)[:200]
        if task is not None:
            task.update({"status": status, "message": message, "error": None if cancelled else str(exc)[:500]})
        try:
            _LAB_STORE.update(task_id, status=status, message=message)
        except Exception:
            _LOGGER.exception("failed to persist Lab terminal state task_id=%s", task_id)


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
            {"id": "is", "name": "净成本 IS", "desc": "冻结 IS 第一名，禁止按 OOS 换人"},
            {"id": "oos", "name": "净成本 OOS", "desc": "主候选一次性样本外验证"},
            {"id": "wf", "name": "三窗 WF", "desc": "完整性、交易数、回撤和稳定性"},
            {"id": "base", "name": "双基线", "desc": "固定种子随机与 MA20/60"},
            {"id": "report", "name": "可信报告", "desc": "PASS/FAIL/证据不足；仅隔离候选"},
        ],
        "disclaimer": "研究辅助，不是投资建议。PASS 只登记隔离候选，不会进入 A 池或直接下单。",
    }


@app.post("/api/lab/optimize")
def lab_optimize(req: LabOptimizeRequest):
    """Start, resume or reuse a persistent trusted Lab validation run."""
    running = _running_task_id()
    if running:
        raise HTTPException(status_code=409, detail=f"已有扫描进行中（{running}），优化任务排队等扫描完成")
    lab_run = _lab_running()
    if lab_run:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LAB_TASK_ALREADY_RUNNING",
                "message": "已有优化任务进行中",
                "active_task_id": lab_run,
                "retryable": True,
            },
        )
    windows = _resolve_lab_windows(req)
    if windows.get("mode") == "insufficient":
        raise HTTPException(
            status_code=400,
            detail="日线覆盖不足，无法启动优化。请更新 Token 后 python sync_history.py",
        )
    request_data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    force = bool(request_data.pop("force", False))
    mode = str(request_data.get("mode") or "grid").lower()
    request_data["mode"] = mode
    if mode == "single":
        required = ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
        if any(request_data.get(key) is None for key in required):
            raise HTTPException(status_code=422, detail="单组试跑参数不完整")
        request_data["grid"] = None
    else:
        grid = request_data.get("grid")
        if isinstance(grid, dict):
            clean_grid = {
                key: list(values)[:8]
                for key, values in grid.items()
                if key in ("vol_ratio_min", "strong_reset", "exit_window", "stop_pct")
                and isinstance(values, list) and values
            }
            combinations = 1
            for values in clean_grid.values():
                combinations *= len(values)
            if combinations > 120:
                raise HTTPException(status_code=422, detail=f"网格组合过多({combinations}>120)")
            request_data["grid"] = clean_grid or None

    from ab_screener.research.trusted_run import (
        COST_VERSION,
        dataset_fingerprint,
        input_fingerprint,
    )
    from optimizer import research_universe

    universe = research_universe(max(20, min(int(request_data.get("max_codes") or 200), 4500)), include_delisted=True)
    starts = [str(windows["is_start"]), str(windows["oos_start"])]
    starts.extend(
        str(row.get("train_start"))
        for row in windows.get("wf_windows") or []
        if isinstance(row, dict) and row.get("train_start")
    )
    dataset_version = dataset_fingerprint(
        _store.db_path, start=min(starts), end=str(windows["oos_end"]), codes=universe
    )
    persisted_request = {**request_data, "_windows": windows}
    input_hash = input_fingerprint(
        request_data, windows, dataset_version=dataset_version,
        code_version=_BUILD_VERSION, cost_version=COST_VERSION,
    )
    if not force:
        cached = _LAB_STORE.completed_by_input_hash(input_hash)
        if cached is not None:
            return {
                "status": "cached", "task_id": cached["research_run_id"],
                "strategy": req.strategy, "research_mode": windows.get("mode"),
                "can_claim_edge": cached.get("candidate_eligible", False), "windows": windows,
            }
        resumable = _LAB_STORE.resumable_by_input_hash(input_hash)
        if resumable is not None:
            task_id = str(resumable["research_run_id"])
            with _LAB_LOCK:
                claimed = _LAB_STORE.resume_run(task_id)
                if not claimed:
                    active = _LAB_STORE.latest_active()
                    active_id = active.get("research_run_id") if active else task_id
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "LAB_TASK_ALREADY_RUNNING",
                            "message": "该实验已由另一个请求恢复",
                            "active_task_id": active_id,
                            "retryable": True,
                        },
                    )
                _LAB_TASKS[task_id] = {
                    "status": "pending", "phase": resumable.get("phase") or "IS",
                    "progress": int(resumable.get("progress") or 0), "message": "从持久化检查点恢复",
                    "started_at": resumable.get("started_at"), "strategy": req.strategy, "windows": windows,
                }
            threading.Thread(target=_run_lab_worker, args=(task_id, req, windows), daemon=True).start()
            return {
                "status": "resumed", "task_id": task_id, "strategy": req.strategy,
                "research_mode": windows.get("mode"), "can_claim_edge": False, "windows": windows,
            }
    if len(_LAB_TASKS) > _LAB_TASKS_MAX:
        for tid in [k for k, v in _LAB_TASKS.items() if v.get("status") in ("done", "error", "cancelled")]:
            _LAB_TASKS.pop(tid, None)
            if len(_LAB_TASKS) <= _LAB_TASKS_MAX:
                break
    task_id = uuid.uuid4().hex[:12]
    from ab_screener.research.store import ActiveResearchRunError

    with _LAB_LOCK:
        try:
            _LAB_STORE.create_run(
                task_id,
                strategy=req.strategy,
                research_mode=str(windows.get("mode") or "manual"),
                request=persisted_request,
                input_hash=input_hash,
                dataset_version=dataset_version,
                code_version=_BUILD_VERSION,
                cost_version=COST_VERSION,
                config_hash=input_hash,
            )
        except ActiveResearchRunError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LAB_TASK_ALREADY_RUNNING",
                    "message": "已有优化任务进行中",
                    "active_task_id": exc.active_run_id,
                    "retryable": True,
                },
            ) from exc
        _LAB_TASKS[task_id] = {
            "status": "pending",
            "phase": "IS",
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
        persisted = _LAB_STORE.get(task_id)
        if persisted is not None:
            return _lab_public_record(persisted)
        runtime = _LAB_TASKS.get(task_id)
        if runtime is not None:
            return {"task_id": task_id, **runtime}
        raise HTTPException(status_code=404, detail="任务不存在")

    persisted_active = _LAB_STORE.latest_active()
    if persisted_active is not None:
        return _lab_public_record(persisted_active)
    persisted_latest = _LAB_STORE.latest()
    if persisted_latest is not None:
        return _lab_public_record(persisted_latest)

    selected = _select_lab_task(_LAB_TASKS)
    if selected is not None:
        selected_id, selected_task = selected
        return {"task_id": selected_id, **selected_task}
    return {"task_id": None, "status": "idle"}


def _report_payload(record: dict) -> dict:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    report = result.get("trusted_report") if isinstance(result, dict) else None
    return {
        "research_run_id": record.get("research_run_id"),
        "status": record.get("status"),
        "strategy": record.get("strategy"),
        "research_mode": record.get("research_mode"),
        "verdict": record.get("verdict"),
        "candidate_eligible": bool(record.get("candidate_eligible")),
        "created_at": record.get("created_at"),
        "finished_at": record.get("finished_at"),
        "report_sha256": record.get("report_sha256"),
        "report": report,
    }


@app.get("/api/lab/reports/latest")
def lab_latest_report():
    reports = _LAB_STORE.list_reports(limit=1)
    if not reports:
        raise HTTPException(status_code=404, detail="暂无可信研究报告")
    return _report_payload(reports[0])


@app.get("/api/lab/reports")
def lab_reports(limit: int = 20):
    return {
        "items": [
            {key: value for key, value in _report_payload(record).items() if key != "report"}
            for record in _LAB_STORE.list_reports(limit=limit)
        ]
    }


@app.get("/api/lab/reports/{research_run_id}")
def lab_report(research_run_id: str):
    record = _LAB_STORE.get(research_run_id)
    if record is None or not record.get("report_markdown"):
        raise HTTPException(status_code=404, detail="报告不存在")
    return _report_payload(record)


@app.get("/api/lab/reports/{research_run_id}/download")
def lab_report_download(research_run_id: str, format: str = "markdown"):
    record = _LAB_STORE.get(research_run_id)
    if record is None or not record.get("report_markdown"):
        raise HTTPException(status_code=404, detail="报告不存在")
    if format.lower() == "json":
        body = json.dumps(_report_payload(record), ensure_ascii=False, indent=2, default=str)
        media_type = "application/json"
        filename = f"lab-report-{research_run_id}.json"
    elif format.lower() in ("markdown", "md"):
        body = str(record["report_markdown"])
        media_type = "text/markdown; charset=utf-8"
        filename = f"lab-report-{research_run_id}.md"
    else:
        raise HTTPException(status_code=422, detail="format 仅支持 markdown 或 json")
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/lab/{task_id}/cancel")
def lab_cancel(task_id: str):
    """取消正在运行的优化任务。"""
    from scan_runtime import is_terminal

    with _LAB_LOCK:
        persisted = _LAB_STORE.get(task_id)
        if persisted is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if is_terminal(persisted.get("status")) or persisted.get("status") == "interrupted":
            return {
                "status": persisted["status"],
                "msg": "任务已结束，无需取消",
                "task_id": task_id,
            }
        persisted = _LAB_STORE.request_cancel(task_id)
        t = _LAB_TASKS.get(task_id)
        if t is not None:
            t["cancel_requested"] = True
            t["status"] = "cancelling"
            t["message"] = "取消中…正在停止工作进程"
    return {
        "status": persisted["status"],
        "task_id": task_id,
        "msg": "取消请求已持久化，正在停止工作进程",
    }


@app.get("/api/lab/leaderboard")
def lab_leaderboard(kind: str = "IS", strategy: str = "A", limit: int = 20):
    """Net-cost leaderboard from the latest persistent Lab result."""
    done = [t for t in _LAB_TASKS.values() if t.get("status") == "done" and t.get("result")]
    in_memory = max(done, key=lambda t: t.get("finished_at") or "") if done else None
    persisted = _LAB_STORE.latest()
    result = in_memory.get("result") if in_memory else None
    if persisted and persisted.get("status") == "done" and persisted.get("result"):
        result = persisted["result"]
    if not isinstance(result, dict):
        return {"rows": [], "source": "empty"}
    rows = result.get("is_top" if kind.upper() == "IS" else "oos") or []
    filtered = [row for row in rows if not strategy or row.get("strategy") == strategy]
    return {"rows": filtered[: max(1, min(limit, 100))], "source": "persistent_trusted_run"}


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

# ═══════════════════════════════════════════════════════════
# 数据同步 API（手动更新行情，2026-08-16 新增）
# ═══════════════════════════════════════════════════════════


@app.post("/api/sync")
def sync_start():
    """触发增量行情同步（后台执行；已有同步进行中返回 409）。"""
    global _SYNC_STATE
    with _SYNC_LOCK:
        if _SYNC_STATE.get("status") == "running":
            raise HTTPException(status_code=409, detail="行情同步已在进行中")
        _SYNC_STATE = {
            "status": "running",
            "message": "开始同步行情…",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "latest_daily": None,
            "latest_moneyflow": None,
            "failed_dates": [],
        }

    def _run() -> None:
        from tushare_init import sanitize_error

        try:
            from local_store import sync_from_tushare

            res = sync_from_tushare(days_back=30, verbose=False)
            failed = (res.get("failed_daily_dates") or []) + (res.get("failed_moneyflow_dates") or [])
            with _SYNC_LOCK:
                _SYNC_STATE.update(
                    status="done" if not failed else "error",
                    message=(
                        f"同步完成：daily 新增 {len(res.get('daily_dates') or [])} 个交易日、"
                        f"moneyflow 新增 {len(res.get('moneyflow_dates') or [])} 个交易日"
                        + (f"；{len(failed)} 个日期失败（可重试）" if failed else "")
                    ),
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                    latest_daily=res.get("latest_daily"),
                    latest_moneyflow=res.get("latest_moneyflow"),
                    failed_dates=failed[:20],
                )
        except Exception as exc:  # noqa: BLE001
            with _SYNC_LOCK:
                _SYNC_STATE.update(
                    status="error",
                    message=f"同步失败：{sanitize_error(exc)[:200]}",
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )

    threading.Thread(target=_run, daemon=True, name="data-sync").start()
    return {"status": "running", "message": "同步已开始"}


@app.get("/api/sync/status")
def sync_status():
    with _SYNC_LOCK:
        return dict(_SYNC_STATE)


# ═══════════════════════════════════════════════════════════
# 回测工作台 API（2026-08-16 新增：单组参数 → IS/OOS 逐笔明细）
# ═══════════════════════════════════════════════════════════


def _bt_prune() -> None:
    if len(_BT_TASKS) > _BT_TASKS_MAX:
        for key in list(_BT_TASKS)[:-_BT_TASKS_MAX]:
            _BT_TASKS.pop(key, None)


@app.post("/api/backtest/run")
def backtest_run(body: dict):
    """启动一次工作台回测（后台执行）。请求体见前端 BacktestStudio。"""
    from ab_screener.research.backtest_engine import run_single_backtest
    from optimizer import ResearchCancelled
    from research_windows import recommend_research_plan
    from walkforward import wf_recheck

    strategy = str(body.get("strategy") or "A")
    exit_p = {
        key: body[key]
        for key in ("vol_ratio_min", "stop_pct", "exit_window", "strong_reset")
        if body.get(key) is not None
    }
    signal_kwargs = {k: v for k, v in (body.get("signal") or {}).items() if v is not None}
    costs = body.get("costs") or None
    max_codes = max(20, min(int(body.get("max_codes") or 600), 4500))
    step = max(1, min(int(body.get("step") or 10), 60))
    include_wf = bool(body.get("include_wf", True))
    include_baselines = bool(body.get("include_baselines", True))

    # 窗口：auto 用研究窗推荐；manual 用显式 IS/OOS
    win = body.get("windows") or {}
    if str(win.get("mode") or "auto") == "manual":
        is_start = str(win.get("is_start") or "")
        is_end = str(win.get("is_end") or "")
        oos_start = str(win.get("oos_start") or "")
        oos_end = str(win.get("oos_end") or "")
        if not (len(is_start) == 8 and len(is_end) == 8 and len(oos_start) == 8 and len(oos_end) == 8):
            raise HTTPException(status_code=422, detail="手动窗口需提供 is_start/is_end/oos_start/oos_end（YYYYMMDD）")
        mode_label = "manual"
        wf_windows = []
    else:
        plan = recommend_research_plan()
        if plan.mode == "insufficient":
            raise HTTPException(status_code=400, detail="日线覆盖不足，无法回测")
        is_start, is_end = plan.is_start, plan.is_end
        oos_start, oos_end = plan.oos_start, plan.oos_end
        mode_label = plan.mode
        wf_windows = plan.wf_windows or []

    task_id = uuid.uuid4().hex[:12]
    with _BT_LOCK:
        _bt_prune()
        _BT_TASKS[task_id] = {
            "status": "running",
            "stage": "准备回测…",
            "progress": 0,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "cancel_requested": False,
            "result": None,
            "error": None,
        }

    def _cancel_flag() -> bool:
        with _BT_LOCK:
            return bool(_BT_TASKS.get(task_id, {}).get("cancel_requested"))

    def _progress(msg: str, pct: int) -> None:
        with _BT_LOCK:
            task = _BT_TASKS.get(task_id)
            if task:
                task["stage"] = msg
                task["progress"] = max(0, min(100, int(pct)))

    def _run() -> None:
        from tushare_init import sanitize_error

        try:
            _progress("IS 样本内回放…", 3)
            is_r = run_single_backtest(
                strategy=strategy, exit_params=exit_p, signal_kwargs=signal_kwargs,
                costs=costs, start=is_start, end=is_end, step=step, max_codes=max_codes,
                progress_cb=lambda m, p: _progress(f"IS · {m}", 3 + int(34 * p / 100)),
                cancel_check=_cancel_flag,
            )
            if is_r.get("error"):
                raise RuntimeError(is_r["error"])
            _progress("OOS 样本外回放…", 40)
            oos_r = run_single_backtest(
                strategy=strategy, exit_params=exit_p, signal_kwargs=signal_kwargs,
                costs=costs, start=oos_start, end=oos_end, step=step, max_codes=max_codes,
                progress_cb=lambda m, p: _progress(f"OOS · {m}", 40 + int(30 * p / 100)),
                cancel_check=_cancel_flag,
            )
            if oos_r.get("error"):
                raise RuntimeError(oos_r["error"])

            wf: dict | None = None
            if include_wf and wf_windows:
                _progress("Walk-forward 三窗复核…", 72)
                combo = {"strategy": strategy, 
                    "vol_ratio_min": exit_p.get("vol_ratio_min", 1.5),
                    "stop_pct": exit_p.get("stop_pct", 0.07),
                    "exit_window": exit_p.get("exit_window", 10),
                    "strong_reset": exit_p.get("strong_reset", 3)
                }
                wf_df = wf_recheck(
                    [combo], step=step, max_codes=max_codes, windows=wf_windows,
                    progress_cb=lambda m, p: _progress(f"WF · {m}", 72 + int(18 * p / 100)),
                    signal_kwargs=signal_kwargs, costs=costs,
                    cancel_check=_cancel_flag,
                )
                if not wf_df.empty:
                    wf = wf_df.iloc[0].to_dict()
                    wf = {k: v.item() if hasattr(v, "item") else v for k, v in wf.items()}
                _progress("Walk-forward 完成", 90)

            baselines: dict | None = None
            if include_baselines:
                from ab_screener.research.baselines import ma_cross_baseline, random_baseline_trades
                from local_store import LocalStore

                oos_metrics = oos_r.get("metrics") or {}
                requested = max(20, int(oos_metrics.get("net_n_trades") or 40))
                hold_days = int(exit_p.get("exit_window") or 10)
                load_start = (pd.to_datetime(oos_start) - pd.Timedelta(days=365)).strftime("%Y%m%d")
                daily = LocalStore().load_daily(
                    ts_codes=None, start=load_start, end=oos_end
                )
                from optimizer import research_universe

                universe = research_universe(max_codes, include_delisted=True)
                baselines = {
                    "random": random_baseline_trades(
                        daily, n_trades=requested, hold_days=hold_days,
                        entry_start=oos_start, entry_end=oos_end, codes=universe,
                    ),
                    "ma20_60": ma_cross_baseline(
                        daily, hold_days=hold_days, max_trades=requested,
                        entry_start=oos_start, entry_end=oos_end, codes=universe,
                    ),
                }
                _progress("基线对比完成", 94)

            is_metrics = is_r.get("metrics") or {}
            oos_metrics = oos_r.get("metrics") or {}
            is_pf = is_metrics.get("net_profit_factor")
            oos_pf = oos_metrics.get("net_profit_factor")
            result = {
                "task_id": task_id,
                "params": {
                    "strategy": strategy,
                    "exit": exit_p,
                    "signal": signal_kwargs or None,
                    "costs": costs,
                    "max_codes": max_codes,
                    "step": step,
                },
                "windows": {
                    "mode": mode_label,
                    "is": [is_start, is_end],
                    "oos": [oos_start, oos_end],
                },
                "is": is_r,
                "oos": oos_r,
                "hold_ratio": {
                    "pf": round(oos_pf / is_pf, 3) if is_pf and oos_pf is not None else None,
                },
                "wf": wf,
                "baselines": baselines,
                "disclaimer": "研究辅助，不是投资建议；宇宙包含上市+退市全历史（已消除幸存者偏差），历史回测结果更保守可信。",
            }
            with _BT_LOCK:
                task = _BT_TASKS.get(task_id, {})
                task.update(status="done", progress=100, stage="回测完成", result=result)
        except ResearchCancelled:
            with _BT_LOCK:
                _BT_TASKS[task_id].update(status="cancelled", stage="已取消")
        except Exception as exc:  # noqa: BLE001
            with _BT_LOCK:
                _BT_TASKS[task_id].update(
                    status="error", stage="回测失败", error=sanitize_error(exc)[:300],
                )

    threading.Thread(target=_run, daemon=True, name=f"bt-{task_id[:6]}").start()
    return {"task_id": task_id}


@app.get("/api/backtest/status/{task_id}")
def backtest_status(task_id: str):
    with _BT_LOCK:
        task = _BT_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return dict(task)


@app.post("/api/backtest/{task_id}/cancel")
def backtest_cancel(task_id: str):
    with _BT_LOCK:
        task = _BT_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        task["cancel_requested"] = True
        if task.get("status") == "running":
            task["stage"] = "取消中…"
        return {"ok": True}


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


# ── 自动日终调度器（阶段5）：交易日 16:15 后轮询，每账户/交易日最多成功一次 ──

def _auto_settle_loop() -> None:
    """后台线程：每日 16:15 后尝试对最近已完成交易日执行日结；幂等（已 DONE 跳过）。"""
    import time as _t
    from zoneinfo import ZoneInfo as _ZI

    tz = _ZI("Asia/Shanghai")
    while True:
        try:
            now = datetime.now(tz)
            latest_local = _store.max_trade_date("daily") or ""
            today = now.strftime("%Y%m%d")
            after_close = now.hour > 16 or (now.hour == 16 and now.minute >= 15)
            # 当天收盘后正常运行；周末/重启时补跑本地最新已完成交易日。
            if latest_local and (after_close or latest_local < today):
                from paper_trading.cal import is_open as _cal_is_open
                from paper_trading.settlement import run_settlement

                try:
                    target = latest_local
                    if _cal_is_open(_DB, target):
                        from ab_screener.data.paper_query import last_done_cycle_date

                        last_done = last_done_cycle_date(_DB)
                        if last_done and last_done >= target:
                            pass  # 已日结，跳过
                        else:
                            try:
                                run_settlement(_DB, target)
                            except Exception as exc:  # noqa: BLE001
                                from tushare_init import sanitize_error
                                _LOGGER.warning("纸面日结待重试 %s: %s", target,
                                                sanitize_error(exc)[:240])
                except Exception as exc:  # noqa: BLE001
                    from tushare_init import sanitize_error
                    _LOGGER.error("纸面调度检查失败: %s", sanitize_error(exc)[:240])
        except Exception as exc:  # noqa: BLE001
            from tushare_init import sanitize_error
            _LOGGER.error("纸面调度循环失败: %s", sanitize_error(exc)[:240])
        _t.sleep(60)  # 每分钟轮询


if _paper_enabled():
    threading.Thread(target=_auto_settle_loop, daemon=True, name="paper-auto-settle").start()


def _backend_port() -> int:
    raw = os.environ.get("AB_BACKEND_PORT", "8001").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("AB_BACKEND_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("AB_BACKEND_PORT must be between 1 and 65535")
    return port




if __name__ == "__main__":
    import uvicorn

    _port = _backend_port()
    print(
        f"UI: http://127.0.0.1:{_port}/  "
        f"(dist={'yes' if _HAS_DIST else 'no-use :3001'})"
    )
    uvicorn.run(app, host="127.0.0.1", port=_backend_port())
