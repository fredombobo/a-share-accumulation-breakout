"""legacy 扫描路由（G2 拆路由第 3 步）。

迁自 web/backend_app.py 的扫描域：后台 worker（子进程 + 进度文件 + 取消整树）、
ScanRequest、scan_status / scan_cancel / scan_runs / scan_run_detail / start_scan。
共享状态（扫描任务字典/取消事件/锁）从 ab_screener.api.legacy_state import；
子进程 spawn 与持久化走 ab_screener.application（scan_spawn / scan_jobs）。

V2R-S 生产接线：扫描完成后把六形态不可变观察落库（`persist_scan_signals`），
受 `V2_STRATEGY_REGISTRY_ENABLED` 门控（默认 false → no-op）。
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ab_screener.api.legacy_state import (
    _BUILD_VERSION,
    _OVERVIEW_CACHE,
    _PARENT,
    _SCAN_CANCEL_EVENTS,
    _SCAN_LOCK,
    _SCAN_TASKS,
    _SCAN_TASKS_MAX,
    _store,
)

router = APIRouter(tags=["legacy"])


# ── V2R-S：扫描完成后信号观察持久化（门控默认关闭 → no-op） ──


def _signal_persistence_enabled() -> bool:
    """V2_STRATEGY_REGISTRY_ENABLED（默认 false）：六形态观察落库门控。

    fail-closed：配置解析失败一律视为关闭，不读行情也不写库。
    """
    try:
        from ab_screener.application.platform_config import (
            flag_enabled,
            load_resolved_config,
        )

        resolved = load_resolved_config()
        return flag_enabled(resolved, "V2_STRATEGY_REGISTRY_ENABLED")
    except Exception:  # noqa: BLE001
        return False


def _candidate_codes_from_result(result: dict) -> list[str]:
    """从扫描结果抽取候选代码（hits + pool_report），去重保序。"""
    codes: list[str] = []
    serialized = result.get("candidate_codes")
    if isinstance(serialized, list):
        codes.extend(str(code) for code in serialized if str(code))
    raw = result.get("hits")
    if isinstance(raw, list):
        codes.extend(str(c) for c in raw if str(c))
    pool = result.get("pool_report") or {}
    if isinstance(pool, dict):
        for key in ("pool_a", "pool_b", "a_codes", "b_codes"):
            val = pool.get(key)
            if isinstance(val, list):
                codes.extend(
                    str(c.get("ts_code") if isinstance(c, dict) else c)
                    for c in val if c
                )
    seen: set[str] = set()
    out: list[str] = []
    for code in codes:
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _read_daily_bars(
    db_path: str | Path, ts_code: str, *, as_of: str = ""
) -> Any:
    """从生产 daily 表读取某标的近期行情（门控开启后由 persist_scan_signals 使用）。

    仅读操作数 daily 表（与 legacy 扫描同源），不读 PIT history、不写任何数据。
    `as_of` 提供时按 `trade_date <= as_of` 截断（防未来函数：不用扫描日之后的 K 线）。
    """
    import pandas as pd

    from ab_screener.data.db import connect

    sql = (
        "SELECT trade_date, open, high, low, close, vol, amount"
        " FROM daily WHERE ts_code=?"
    )
    params: list[Any] = [ts_code]
    if as_of:
        sql += " AND trade_date <= ?"
        params.append(str(as_of))
    sql += " ORDER BY trade_date DESC LIMIT 250"
    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(
        rows,
        columns=["date", "open", "high", "low", "close", "vol", "amount"],
    )
    df["date"] = df["date"].astype(str)
    return df.sort_values("date").reset_index(drop=True)


def persist_scan_signals(
    db_path: str | Path,
    *,
    scan_run_id: str,
    candidate_codes: list[str],
    strategy_version: str = "v1",
    bars_reader: Callable[[str], Any] | None = None,
    as_of: str = "",
) -> dict[str, Any]:
    """扫描完成后把六形态观察落库（scan_run_id 作 snapshot_id，重放幂等）。

    - 受 V2_STRATEGY_REGISTRY_ENABLED 门控：默认 false → no-op（不读行情不写库）。
    - 只写不可变 `signal_observations`；不改变 A/B 池、买入草稿或目标仓位。
    - 同 scan_run_id + strategy_version + instrument 重放 → 不产生新 observation。
    - 单标的异常被隔离，不影响其他标的。
    """
    if not _signal_persistence_enabled():
        return {"enabled": False, "scan_run_id": scan_run_id, "persisted": 0}
    if not candidate_codes:
        return {"enabled": True, "scan_run_id": scan_run_id, "persisted": 0}
    if bars_reader is None:
        return {
            "enabled": True,
            "scan_run_id": scan_run_id,
            "persisted": 0,
            "error": "bars_reader 未提供",
        }
    from ab_screener.application.signal_pipeline import run_signal_pipeline
    from ab_screener.data.db import connect

    saved: list[str] = []
    errors: dict[str, str] = {}
    with connect(db_path) as conn:
        for code in candidate_codes:
            try:
                bars = bars_reader(code)
                if bars is None or len(bars) == 0:
                    continue
                input_hash = _bars_input_hash(
                    bars,
                    strategy_version=strategy_version,
                    ts_code=code,
                    as_of=as_of,
                )
                result = run_signal_pipeline(
                    conn,
                    bars=bars,
                    ts_code=code,
                    snapshot_id=scan_run_id,
                    input_hash=input_hash,
                )
                saved.extend(result["saved_observation_ids"])
                errors.update(result["errors"])
            except Exception as exc:  # noqa: BLE001
                errors[code] = f"{type(exc).__name__}: {exc}"
    return {
        "enabled": True,
        "scan_run_id": scan_run_id,
        "as_of": as_of,
        "persisted": len(saved),
        "saved_observation_ids": saved,
        "errors": errors,
    }


def _bars_input_hash(
    bars: Any,
    *,
    strategy_version: str,
    ts_code: str,
    as_of: str,
) -> str:
    """对实际行情输入生成确定性 hash，修订数据必须形成新 observation。"""
    import hashlib
    import json

    if hasattr(bars, "columns") and hasattr(bars, "to_json"):
        columns = sorted(str(column) for column in bars.columns)
        normalized = bars.loc[:, columns].copy()
        for date_column in ("date", "trade_date"):
            if date_column in normalized.columns:
                normalized[date_column] = normalized[date_column].astype(str)
                normalized = normalized.sort_values(date_column).reset_index(drop=True)
                break
        bars_payload = normalized.to_json(
            orient="split",
            date_format="iso",
            double_precision=15,
        )
    else:
        bars_payload = json.dumps(bars, sort_keys=True, ensure_ascii=False, default=str)
    payload = f"{strategy_version}|{ts_code}|{as_of}|{bars_payload}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _clear_overview_cache() -> None:
    """新扫描完成时清空总览轻量缓存，避免前端读到旧扫描结果。"""
    _OVERVIEW_CACHE["key"] = None
    _OVERVIEW_CACHE["payload"] = None


def _new_task(top: int, days: int) -> str:
    task_id = uuid.uuid4().hex[:12]
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with _SCAN_LOCK:
        _SCAN_TASKS[task_id] = {
            "id": task_id,
            "top": top,
            "days": days,
            "status": "pending",
            "stage": "排队中",
            "progress": 0,
            "created_at": created_at,
            "started_at": None,
            "updated_at": created_at,
            "heartbeat_at": created_at,
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
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            next_progress = clamp_progress(progress)
            if t.get("cancel_requested") or t.get("status") == "cancelling":
                t["status"] = "cancelling"
                base = stage or t.get("stage") or ""
                next_stage = base if "取消" in str(base) else (f"取消中…{base}" if base else "取消中…")
            else:
                t["status"] = "running"
                next_stage = stage
            if t.get("progress") != next_progress or t.get("stage") != next_stage:
                t["updated_at"] = now
            t["stage"] = next_stage
            t["progress"] = next_progress
            t["heartbeat_at"] = now
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
            started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            task["started_at"] = started_at
            task["updated_at"] = started_at
            task["heartbeat_at"] = started_at
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
            finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
            t["finished_at"] = finished_at
            t["updated_at"] = finished_at
            t["heartbeat_at"] = finished_at
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
            _clear_overview_cache()
        # V2R-S 生产接线：扫描完成后把六形态观察落库（门控默认关闭 → no-op）
        try:
            signal_hook = persist_scan_signals(
                _store.db_path,
                scan_run_id=task_id,
                candidate_codes=_candidate_codes_from_result(result),
                as_of=str(result.get("latest_date") or ""),
                bars_reader=lambda code: _read_daily_bars(
                    _store.db_path, code,
                    as_of=str(result.get("latest_date") or ""),
                ),
            )
            if signal_hook.get("persisted"):
                _log(t, f"signal observations persisted: {signal_hook['persisted']}")
            if signal_hook.get("error"):
                _log(t, f"signal persistence rejected: {signal_hook['error']}")
            if signal_hook.get("errors"):
                _log(t, f"signal persistence partial errors: {signal_hook['errors']}")
        except Exception as exc:  # noqa: BLE001
            _log(t, f"signal persistence failed: {type(exc).__name__}: {exc}")
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

@router.get("/api/scan/status")
def scan_status(task_id: str | None = None):
    """查询扫描进度。默认返回最新任务；指定 task_id 返回该任务。"""
    with _SCAN_LOCK:
        if task_id:
            task = _SCAN_TASKS.get(task_id)
            if task is None:
                task = None
            else:
                keys = (
                    "id", "status", "stage", "progress", "cancel_requested", "result",
                    "error", "worker_pid", "created_at", "started_at", "updated_at",
                    "heartbeat_at", "finished_at",
                )
                return {k: task.get(k) for k in keys}
        elif _SCAN_TASKS:
            # 返回最新内存任务
            latest = max(_SCAN_TASKS.values(), key=lambda t: t.get("started_at") or "")
            keys = (
                "id", "status", "stage", "progress", "cancel_requested", "result",
                "error", "worker_pid", "created_at", "started_at", "updated_at",
                "heartbeat_at", "finished_at",
            )
            return {k: latest.get(k) for k in keys}

    # 服务重启后从持久任务表恢复查询语义。
    from ab_screener.application.scan_jobs import ScanJobStore, to_api_status

    store = ScanJobStore(_store.db_path)
    job = store.get(task_id) if task_id else store.latest()
    if task_id and not job:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return to_api_status(job)


@router.post("/api/scan/{task_id}/cancel")
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




@router.get("/api/scan/runs")
def list_scan_runs(limit: int = 20):
    """扫描回放列表（upgrade system）。"""
    from ab_screener.data.scan_run_repository import list_scan_runs as _list_runs

    return {"runs": _list_runs(_store.db_path, limit)}


@router.get("/api/scan/runs/{run_id}")
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


@router.post("/api/scan")
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





