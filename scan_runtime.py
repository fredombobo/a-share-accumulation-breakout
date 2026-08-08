"""扫描/优化任务运行时加固工具

- 双重取消信号：threading.Event + task 字典标志（防事件丢失）
- 进程池安全放弃：杀进程 + 后台 shutdown，主线程不阻塞
- 状态机：合法终态收敛，避免永远 pending/running/cancelling
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import Any

TERMINAL = frozenset({"done", "error", "cancelled"})
ACTIVE = frozenset({"pending", "running", "cancelling"})


def is_terminal(status: str | None) -> bool:
    return (status or "") in TERMINAL


def is_active(status: str | None) -> bool:
    return (status or "") in ACTIVE


def cancel_flag_check(task: dict | None, event: threading.Event | None) -> bool:
    """双重取消：Event 或 task.cancel_requested 任一为真即取消。"""
    if event is not None:
        try:
            if event.is_set():
                return True
        except Exception:  # noqa: BLE001
            pass
    return bool(task is not None and task.get("cancel_requested"))


def request_cancel(task: dict, event: threading.Event | None) -> None:
    """幂等：标记取消并 set event。"""
    task["cancel_requested"] = True
    if task.get("status") not in TERMINAL:
        task["status"] = "cancelling"
        if "取消" not in str(task.get("stage") or ""):
            task["stage"] = "取消中…正在停止工作进程"
    task["cancel_requested_at"] = task.get("cancel_requested_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
    if event is not None:
        try:
            event.set()
        except Exception:  # noqa: BLE001
            pass


def force_terminal(
    task: dict,
    status: str,
    *,
    stage: str | None = None,
    error: str | None = None,
    log_fn: Callable[[dict, str], None] | None = None,
    msg: str = "",
) -> None:
    """强制进入终态（watchdog / finally 收口）。"""
    if task.get("status") in TERMINAL and status != "error":
        # 已终态：error 可覆盖? 一般不覆盖 cancelled/done
        return
    task["status"] = status
    if stage:
        task["stage"] = stage
    if error is not None:
        task["error"] = error
    task["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if log_fn and msg:
        log_fn(task, msg)


def start_cancel_watchdog(
    *,
    task_id: str,
    get_task: Callable[[], dict | None],
    lock: threading.Lock,
    timeout_sec: float = 10.0,
    prune: Callable[[], None] | None = None,
    log_fn: Callable[[dict, str], None] | None = None,
) -> None:
    """超时仍未终态 → 强制 cancelled，解锁互斥。"""

    def _run() -> None:
        time.sleep(max(0.05, float(timeout_sec)))
        with lock:
            t = get_task()
            if t is None:
                return
            if t.get("cancel_requested") and is_active(t.get("status")):
                force_terminal(
                    t,
                    "cancelled",
                    stage="已取消（超时强制结束）",
                    log_fn=log_fn,
                    msg=f"cancel watchdog force-closed after {timeout_sec:.0f}s",
                )
        if prune:
            try:
                prune()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, daemon=True, name=f"cancel-wd-{task_id}").start()


def terminate_pool_processes(pool: ProcessPoolExecutor) -> None:
    try:
        procs = getattr(pool, "_processes", None) or {}
        for p in list(procs.values()):
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                if getattr(p, "is_alive", lambda: False)():
                    p.kill()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def abandon_pool(pool: ProcessPoolExecutor) -> None:
    """杀进程后后台 shutdown，主线程立即返回。"""
    terminate_pool_processes(pool)

    def _bg() -> None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            try:
                pool.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        try:
            terminate_pool_processes(pool)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_bg, daemon=True, name="pool-abandon").start()


def safe_workers(workers: int | None = None, *, hard_cap: int = 8) -> int:
    """限制并行度，降低内存/句柄打爆风险（稳健优先）。"""
    cpu = os.cpu_count() or 4
    auto = max(1, min(hard_cap, cpu - 1 if cpu > 2 else cpu))
    if workers is None or workers <= 0:
        return auto
    return max(1, min(hard_cap, int(workers)))


def clamp_progress(p: Any) -> int:
    try:
        return max(0, min(100, int(p)))
    except (TypeError, ValueError):
        return 0


def kill_process_tree(pid: int | None) -> bool:
    """强制结束进程树（Windows taskkill /T；其它平台 os.kill）。返回是否尝试过。"""
    if not pid or pid <= 0:
        return False
    import signal
    import subprocess

    try:
        if os.name == "nt":
            # /T 杀整棵子树（含 ProcessPool worker）
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(int(pid))],
                capture_output=True,
                timeout=8,
                check=False,
            )
            return True
        os.kill(int(pid), signal.SIGTERM)
        return True
    except Exception:  # noqa: BLE001
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(int(pid))],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            else:
                os.kill(int(pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            return False
        return True
