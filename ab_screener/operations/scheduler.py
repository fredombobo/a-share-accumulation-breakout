"""调度执行器（P6.1）：按 DAG 顺序执行、幂等、attempt 重试、崩溃续跑。"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from ab_screener.data.scheduler_repository import (
    last_completed_steps,
    mark_run_finished,
    record_step_attempt,
    start_run,
    step_attempt_status,
)
from ab_screener.operations.dag import DailyDag, StepSpec, idempotency_key


class SchedulerRunner:
    """串行执行 DAG 步骤；每步至多 max_attempts 次；崩溃后可续跑。"""

    def __init__(self, db_path: str, dag: DailyDag):
        self.db_path = db_path
        self.dag = dag

    def run_day(
        self,
        trade_date: str,
        *,
        mode: str = "EOD",
        scope_type: str = "GLOBAL",
        scope_id: str = "all",
        input_hash: str = "default",
        attempt_provider: Callable[[str], int] | None = None,
        resume_from_failures: bool = True,
    ) -> dict[str, Any]:
        """执行整日 DAG。返回 {run_id, results, status}。"""
        with sqlite3.connect(self.db_path) as conn:
            run_id = start_run(conn, trade_date=trade_date, mode=mode)
            completed = last_completed_steps(conn, run_id)

        results: dict[str, Any] = {}
        for step in self.dag.steps:
            # 上游依赖必须已成功（FAIL 或 SKIPPED 均阻断）
            deps = self.dag.dependencies_of(step.name)
            failed_deps = [d for d in deps
                           if results.get(d, {}).get("status") in ("FAIL", "SKIPPED")]
            if failed_deps:
                results[step.name] = {"status": "SKIPPED",
                                      "reason": f"上游失败: {failed_deps}"}
                continue
            key = idempotency_key(trade_date, step.name, scope_type, scope_id, input_hash)
            with sqlite3.connect(self.db_path) as conn:
                attempt, status = step_attempt_status(conn, key=key)
            if status == "SUCCESS":
                results[step.name] = {"status": "SUCCESS", "attempt": attempt,
                                      "idempotent": True}
                continue
            if step.name in completed and resume_from_failures:
                results[step.name] = {"status": "SUCCESS", "resumed": True}
                continue

            outcome = self._execute_with_retry(
                step, trade_date=trade_date, run_id=run_id,
                scope_type=scope_type, scope_id=scope_id, input_hash=input_hash,
                attempt=attempt,
            )
            results[step.name] = outcome

        final = "COMPLETED" if all(
            r.get("status") in ("SUCCESS", "SKIPPED") for r in results.values()
        ) else "FAILED"
        with sqlite3.connect(self.db_path) as conn:
            mark_run_finished(conn, run_id, final)
        return {"run_id": run_id, "results": results, "status": final}

    def _execute_with_retry(
        self, step: StepSpec, *, trade_date: str, run_id: str,
        scope_type: str, scope_id: str, input_hash: str, attempt: int,
    ) -> dict[str, Any]:
        for i in range(attempt + 1, self.dag.max_attempts + 1):
            with sqlite3.connect(self.db_path) as conn:
                record_step_attempt(
                    conn, run_id=run_id, trade_date=trade_date, step_name=step.name,
                    scope_type=scope_type, scope_id=scope_id, input_hash=input_hash,
                    attempt=i, status="RUNNING",
                )
            try:
                step.fn(trade_date=trade_date)
            except Exception as exc:  # noqa: BLE001
                last = (i == self.dag.max_attempts)
                with sqlite3.connect(self.db_path) as conn:
                    record_step_attempt(
                        conn, run_id=run_id, trade_date=trade_date, step_name=step.name,
                        scope_type=scope_type, scope_id=scope_id, input_hash=input_hash,
                        attempt=i, status="FAIL" if last else "ATTEMPT_FAILED",
                        error=str(exc),
                    )
                if last:
                    return {"status": "FAIL", "attempt": i, "error": str(exc)}
                continue
            with sqlite3.connect(self.db_path) as conn:
                record_step_attempt(
                    conn, run_id=run_id, trade_date=trade_date, step_name=step.name,
                    scope_type=scope_type, scope_id=scope_id, input_hash=input_hash,
                    attempt=i, status="SUCCESS",
                )
            return {"status": "SUCCESS", "attempt": i}
        return {"status": "FAIL", "error": "no attempts left"}
