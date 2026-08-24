"""调度执行器（V2R-O2）：按显式 DAG 依赖执行、租约包围、attempt 保留、崩溃续跑、审计。

- 租约（包围控制）：run_day 开始时原子抢占同账户/交易日租约；未获租约 →
  LEASE_CONFLICT 且不产生 run；执行中续租；退出 finally 释放。
- 审计（包围控制）：RUN_START / STEP_FAILED / RUN_FINISHED 写入 append-only
  hash chain；审计失败 → fail-closed，run 标记 FAILED（不产生 COMPLETE 证据）。
- attempt：进程重启后从已记录 attempt 继续（不重数），末次 FAIL 且达
  max_attempts → EXHAUSTED 禁止再执行；崩溃遗留 RUNNING → 同 attempt 覆盖重试。
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ab_screener.data.scheduler_repository import (
    last_completed_steps,
    lease_id_for,
    mark_run_finished,
    record_step_attempt,
    release_lease,
    renew_lease,
    start_run,
    step_attempt_status,
)
from ab_screener.operations.dag import DailyDag, StepSpec, idempotency_key


def _error_text(exc: Exception) -> str:
    """结构化错误文本：带 code 的异常（DomainError/DagStepError）保留 code 前缀。"""
    code = getattr(exc, "code", None)
    text = str(exc)
    if code and not text.startswith(str(code)):
        return f"{code}: {text}"
    return text


class SchedulerRunner:
    """串行执行 DAG 步骤；至多 max_attempts 次；崩溃可续跑；并发单租约。"""

    def __init__(
        self,
        db_path: str,
        dag: DailyDag,
        *,
        holder: str | None = None,
        lease_ttl_seconds: int = 300,
        signing_key: bytes | None = None,
        audit_anchor_dir: str | None = None,
    ):
        self.db_path = db_path
        self.dag = dag
        self.holder = holder or f"scheduler-{os.getpid()}"
        self.lease_ttl_seconds = lease_ttl_seconds
        self.signing_key = signing_key
        self.audit_anchor_dir = audit_anchor_dir

    # ── 审计（fail-closed 包围控制） ──

    def _audit(
        self, conn: sqlite3.Connection, action: str, run_id: str,
        trade_date: str, **extra: Any,
    ) -> str:
        from ab_screener.application.audit_service import record_audit_event

        return record_audit_event(
            conn,
            actor=self.holder,
            action=action,
            request={
                "trade_date": trade_date,
                "scope_type": "ACCOUNT",
                "scope_id": "1",
                "dag": list(self.dag.order()),
                **extra,
            },
            correlation_id=run_id or trade_date,
        )

    def _audit_run_started(self, run_id: str, trade_date: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            self._audit(conn, "DAG_RUN_START", run_id, trade_date)

    def _audit_step_failed(self, run_id: str, trade_date: str, step_name: str,
                           error: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            self._audit(conn, "DAG_STEP_FAILED", run_id, trade_date,
                        step=step_name, error=error)

    def _audit_run_finished(self, run_id: str, trade_date: str, status: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            self._audit(conn, "DAG_RUN_FINISHED", run_id, trade_date, status=status)
        if self.audit_anchor_dir:
            from ab_screener.application.audit_service import sign_chain_head

            with sqlite3.connect(self.db_path) as conn:
                sign_chain_head(
                    conn, self.audit_anchor_dir, signing_key=self.signing_key,
                )

    # ── 主入口 ──

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
        lease_holder: str | None = None,
        today: str | None = None,
    ) -> dict[str, Any]:
        """执行整日 DAG。返回 {run_id, results, status}；租约冲突 → LEASE_CONFLICT。"""
        holder = lease_holder or self.holder
        lease_id = lease_id_for(trade_date, scope_type, scope_id)
        ctx: dict[str, Any] = {
            "db_path": Path(self.db_path),
            "today": today,
            "results": {},
        }

        with sqlite3.connect(self.db_path) as conn:
            from ab_screener.data.scheduler_repository import acquire_lease

            acquired = acquire_lease(
                conn, lease_id=lease_id, holder=holder, trade_date=trade_date,
                ttl_seconds=self.lease_ttl_seconds,
            )
        if not acquired:
            return {
                "run_id": None,
                "status": "LEASE_CONFLICT",
                "holder": holder,
                "reason": f"租约 {lease_id} 被其他 holder 持有（未过期）",
            }

        run_id: str | None = None
        results: dict[str, Any] = {}
        final = "FAILED"
        try:
            with sqlite3.connect(self.db_path) as conn:
                run_id = start_run(conn, trade_date=trade_date, mode=mode)
                completed = last_completed_steps(
                    conn, run_id, input_hash=input_hash,
                )
            self._audit_run_started(run_id, trade_date)

            for step in self.dag.steps:
                if run_id is None:
                    break
                # 上游依赖必须已成功（FAIL 或 SKIPPED 均阻断全部下游）
                deps = self.dag.dependencies_of(step.name)
                failed_deps = [d for d in deps
                               if results.get(d, {}).get("status") in ("FAIL", "SKIPPED")]
                if failed_deps:
                    results[step.name] = {"status": "SKIPPED",
                                          "reason": f"上游失败: {failed_deps}"}
                    continue
                key = idempotency_key(trade_date, step.name, scope_type, scope_id,
                                      input_hash)
                with sqlite3.connect(self.db_path) as conn:
                    attempt, status = step_attempt_status(conn, key=key)
                if status == "SUCCESS":
                    results[step.name] = {"status": "SUCCESS", "attempt": attempt,
                                          "idempotent": True}
                    continue
                if status == "EXHAUSTED":
                    results[step.name] = {
                        "status": "FAIL", "attempt": attempt,
                        "error": f"已达 max_attempts={self.dag.max_attempts}，"
                                 f"第 {attempt + 1} 次不得执行",
                    }
                    continue
                if step.name in completed and resume_from_failures:
                    results[step.name] = {"status": "SUCCESS", "resumed": True}
                    continue
                # 每步前续租（长步骤期间租约续期；同 holder 才能续）
                with sqlite3.connect(self.db_path) as conn:
                    renew_lease(conn, lease_id=lease_id, holder=holder,
                                ttl_seconds=self.lease_ttl_seconds)
                outcome = self._execute_with_retry(
                    step, trade_date=trade_date, run_id=run_id,
                    scope_type=scope_type, scope_id=scope_id, input_hash=input_hash,
                    attempt=attempt, status=status, ctx=ctx,
                )
                ctx["results"][step.name] = outcome
                results[step.name] = outcome
                if outcome.get("status") == "FAIL":
                    self._audit_step_failed(run_id, trade_date, step.name,
                                            str(outcome.get("error") or ""))

            final = "COMPLETED" if all(
                r.get("status") in ("SUCCESS", "SKIPPED") for r in results.values()
            ) else "FAILED"
            with sqlite3.connect(self.db_path) as conn:
                if run_id is not None:
                    mark_run_finished(conn, run_id, final)
            self._audit_run_finished(run_id or trade_date, trade_date, final)
            return {"run_id": run_id, "results": results, "status": final}
        except Exception as exc:  # noqa: BLE001 fail-closed：审计失败/意外异常 → FAILED
            try:
                if run_id is not None:
                    with sqlite3.connect(self.db_path) as conn:
                        mark_run_finished(conn, run_id, "FAILED")
                self._audit_step_failed(run_id or "unknown", trade_date,
                                        "RUN", _error_text(exc))
            except Exception:  # noqa: BLE001 审计表不可用时不掩盖原始错误
                pass
            return {
                "run_id": run_id,
                "results": results,
                "status": "FAILED",
                "error": _error_text(exc),
            }
        finally:
            with sqlite3.connect(self.db_path) as conn:
                release_lease(conn, lease_id=lease_id, holder=holder)

    def _execute_with_retry(
        self, step: StepSpec, *, trade_date: str, run_id: str,
        scope_type: str, scope_id: str, input_hash: str, attempt: int,
        status: str, ctx: dict[str, Any],
    ) -> dict[str, Any]:
        # 崩溃遗留 RUNNING → 同 attempt 覆盖重试；失败/无记录 → 从 attempt+1 继续。
        start = attempt if status == "RUNNING" else attempt + 1
        for i in range(start, self.dag.max_attempts + 1):
            with sqlite3.connect(self.db_path) as conn:
                record_step_attempt(
                    conn, run_id=run_id, trade_date=trade_date, step_name=step.name,
                    scope_type=scope_type, scope_id=scope_id, input_hash=input_hash,
                    attempt=i, status="RUNNING",
                )
            try:
                step_result = step.fn(trade_date=trade_date, ctx=ctx)
            except Exception as exc:  # noqa: BLE001
                last = (i == self.dag.max_attempts)
                with sqlite3.connect(self.db_path) as conn:
                    record_step_attempt(
                        conn, run_id=run_id, trade_date=trade_date, step_name=step.name,
                        scope_type=scope_type, scope_id=scope_id, input_hash=input_hash,
                        attempt=i, status="FAIL" if last else "ATTEMPT_FAILED",
                        error=_error_text(exc),
                    )
                if last:
                    return {"status": "FAIL", "attempt": i, "error": _error_text(exc)}
                continue
            with sqlite3.connect(self.db_path) as conn:
                record_step_attempt(
                    conn, run_id=run_id, trade_date=trade_date, step_name=step.name,
                    scope_type=scope_type, scope_id=scope_id, input_hash=input_hash,
                    attempt=i, status="SUCCESS",
                )
            outcome: dict[str, Any] = {"status": "SUCCESS", "attempt": i}
            if isinstance(step_result, dict):
                outcome["step_result"] = step_result
                for k, v in step_result.items():
                    if k not in ("status", "attempt"):
                        outcome[k] = v
            return outcome
        return {"status": "FAIL", "error": "no attempts left"}
