"""scan_runtime 与取消/状态机稳健性单测。"""
from __future__ import annotations

import threading
import time
import unittest

from scan_runtime import (
    ACTIVE,
    TERMINAL,
    cancel_flag_check,
    clamp_progress,
    force_terminal,
    is_active,
    is_terminal,
    request_cancel,
    safe_workers,
    start_cancel_watchdog,
)


class ScanRuntimeTest(unittest.TestCase):
    def test_terminal_sets(self) -> None:
        for s in ("done", "error", "cancelled"):
            self.assertTrue(is_terminal(s))
            self.assertFalse(is_active(s))
        for s in ("pending", "running", "cancelling"):
            self.assertTrue(is_active(s))
            self.assertFalse(is_terminal(s))
        self.assertEqual(TERMINAL & ACTIVE, frozenset())

    def test_dual_cancel_flag(self) -> None:
        ev = threading.Event()
        task = {"cancel_requested": False}
        self.assertFalse(cancel_flag_check(task, ev))
        task["cancel_requested"] = True
        self.assertTrue(cancel_flag_check(task, ev))
        task["cancel_requested"] = False
        ev.set()
        self.assertTrue(cancel_flag_check(task, ev))
        self.assertTrue(cancel_flag_check(None, ev))

    def test_request_cancel_idempotent(self) -> None:
        ev = threading.Event()
        task = {"status": "running", "stage": "预筛", "cancel_requested": False}
        request_cancel(task, ev)
        self.assertTrue(task["cancel_requested"])
        self.assertEqual(task["status"], "cancelling")
        self.assertTrue(ev.is_set())
        self.assertIn("取消", task["stage"])
        # 幂等
        request_cancel(task, ev)
        self.assertEqual(task["status"], "cancelling")

    def test_force_terminal_no_overwrite_done(self) -> None:
        task = {"status": "done", "stage": "完成"}
        force_terminal(task, "cancelled", stage="x")
        self.assertEqual(task["status"], "done")

    def test_force_terminal_from_cancelling(self) -> None:
        task = {"status": "cancelling", "stage": "取消中"}
        force_terminal(task, "cancelled", stage="已取消")
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(task["stage"], "已取消")
        self.assertIn("finished_at", task)

    def test_clamp_progress(self) -> None:
        self.assertEqual(clamp_progress(-1), 0)
        self.assertEqual(clamp_progress(150), 100)
        self.assertEqual(clamp_progress("12"), 12)
        self.assertEqual(clamp_progress("x"), 0)

    def test_safe_workers_cap(self) -> None:
        self.assertGreaterEqual(safe_workers(0), 1)
        self.assertLessEqual(safe_workers(99, hard_cap=8), 8)

    def test_watchdog_force_cancel(self) -> None:
        lock = threading.Lock()
        tasks = {
            "t1": {
                "status": "cancelling",
                "cancel_requested": True,
                "stage": "取消中…预筛",
            }
        }
        start_cancel_watchdog(
            task_id="t1",
            get_task=lambda: tasks.get("t1"),
            lock=lock,
            timeout_sec=0.3,
        )
        time.sleep(0.8)
        self.assertEqual(tasks["t1"]["status"], "cancelled")
        self.assertIn("强制", tasks["t1"]["stage"])


if __name__ == "__main__":
    unittest.main()
