"""
「已有任务时拒绝并发」逻辑测试 —— 离线骨架测试（B14）
====================================================
不 import web.backend_app（该模块正被其他任务并发修改），直接构造同名状态结构
(_SCAN_TASKS / _SCAN_LOCK / _running_task_id)，复刻 start_scan 的并发互斥语义
（已有 pending/running 任务时拒绝新扫描，对应 HTTP 409）。
      **不要执行**（仅 py_compile；最终集成验证由主流程统一跑）。
集成阶段执行方式：
    C:\\Python314\\python.exe -m unittest tests.test_scan_guard
"""
import threading
import unittest

# ── 与 backend_app.py 同构的模块级状态（仅测试用，非真实模块） ──
_SCAN_TASKS: dict[str, dict] = {}
_SCAN_LOCK = threading.Lock()
# backend_app 终止态：done / error / cancelled

_PENDING_OR_RUNNING = ("pending", "running")


def _running_task_id() -> str | None:
    """复刻 backend_app._running_task_id：存在 pending/running 任务则返回其 task_id。"""
    with _SCAN_LOCK:
        for tid, t in _SCAN_TASKS.items():
            if t.get("status") in _PENDING_OR_RUNNING:
                return tid
    return None


def _try_acquire_scan_slot() -> tuple[bool, str | None]:
    """复刻 start_scan 的并发互斥：已有运行/排队任务 → 拒绝。"""
    running = _running_task_id()
    if running:
        return False, running
    return True, None


class ScanGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        _SCAN_TASKS.clear()

    @staticmethod
    def _task(tid: str, status: str) -> dict:
        return {"id": tid, "status": status, "stage": "", "progress": 0,
                "started_at": None, "finished_at": None, "result": None, "error": None}

    def test_empty_allow(self) -> None:
        ok, tid = _try_acquire_scan_slot()
        self.assertTrue(ok)
        self.assertIsNone(tid)

    def test_running_task_rejects(self) -> None:
        _SCAN_TASKS["t1"] = self._task("t1", "running")
        ok, tid = _try_acquire_scan_slot()
        self.assertFalse(ok)
        self.assertEqual(tid, "t1")

    def test_pending_task_rejects(self) -> None:
        _SCAN_TASKS["t1"] = self._task("t1", "pending")
        ok, tid = _try_acquire_scan_slot()
        self.assertFalse(ok)
        self.assertEqual(tid, "t1")

    def test_only_terminal_tasks_allow(self) -> None:
        for i, status in enumerate(("done", "error", "cancelled")):
            _SCAN_TASKS[f"t{i}"] = self._task(f"t{i}", status)
        ok, _ = _try_acquire_scan_slot()
        self.assertTrue(ok)

    def test_mixed_picks_active_task(self) -> None:
        _SCAN_TASKS["old_done"] = self._task("old_done", "done")
        _SCAN_TASKS["running1"] = self._task("running1", "running")
        ok, tid = _try_acquire_scan_slot()
        self.assertFalse(ok)
        self.assertEqual(tid, "running1")

    def test_new_task_not_created_when_rejected(self) -> None:
        """被拒绝时不新增任务（镜像 start_scan 409 分支：不调用 _new_task）。"""
        _SCAN_TASKS["t1"] = self._task("t1", "running")
        ok, _ = _try_acquire_scan_slot()
        if ok:  # 仅在允许时模拟创建新任务
            _SCAN_TASKS["t2"] = self._task("t2", "pending")
        self.assertEqual(len(_SCAN_TASKS), 1)


if __name__ == "__main__":
    unittest.main()
