"""龙虎榜盘后 DAG（T11）。独立于冻结的 DAG_STEPS，mode=LHB_EOD。"""
from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Callable
from typing import Any

from ab_screener.data.scheduler_repository import acquire_lease
from ab_screener.domain.lhb_contracts import CONFIRMED_SIGNAL_STATUSES
from ab_screener.operations.dag import DAG_STEPS, DailyDag, StepSpec
from ab_screener.operations.lhb_alerts import create_alert
from ab_screener.operations.scheduler import SchedulerRunner

# 不得把这些名字写入 operations.dag.DAG_STEPS。
LHB_DAG_STEPS: tuple[str, ...] = (
    "lhb_ingest",
    "lhb_reconcile",
    "lhb_transform",
    "lhb_map",
    "lhb_features",
    "lhb_signals",
    "lhb_report",
    "lhb_alerts",
)

LHB_MODE = "LHB_EOD"
FROZEN_MAIN_DAG_STEPS = DAG_STEPS


def confirmed_blocked(source_status: str) -> bool:
    return source_status in ("FETCH_FAILED", "DEGRADED", "NOT_PUBLISHED", "VALID_EMPTY")


def clamp_signal_status(status: str, source_status: str) -> str:
    if confirmed_blocked(source_status) and status in CONFIRMED_SIGNAL_STATUSES:
        return "WATCH"
    return status


def build_lhb_dag(
    fns: dict[str, Callable[..., Any]] | None = None,
    *,
    max_attempts: int = 3,
) -> DailyDag:
    impl = fns or {}

    def _noop(**_: Any) -> None:
        return None

    def _adapt(fn: Callable[..., Any]) -> Callable[..., Any]:
        """共享 DAG 运行器会向步骤传 ctx；龙虎榜步骤只关心 trade_date。

        运行器签名演进过一次（新增 ctx），直接调用会 TypeError 并让整条流水线
        FAILED。这里按被包装函数实际接受的参数过滤：既不用改各步骤签名，
        运行器将来再加参数也不会二次踩坑。
        """
        params = inspect.signature(fn).parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return fn
        accepted = {
            name
            for name, p in params.items()
            if p.kind
            in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        }

        def _wrapped(**kwargs: Any) -> Any:
            return fn(**{k: v for k, v in kwargs.items() if k in accepted})

        return _wrapped

    steps: list[StepSpec] = []
    prev: str | None = None
    for name in LHB_DAG_STEPS:
        steps.append(
            StepSpec(
                name=name,
                scope_type="GLOBAL",
                scope_id="lhb",
                fn=_adapt(impl.get(name, _noop)),
                depends_on=(prev,) if prev else (),
            )
        )
        prev = name
    return DailyDag(steps, max_attempts=max_attempts)


def try_lease(conn: sqlite3.Connection, *, trade_date: str, holder: str) -> bool:
    return acquire_lease(
        conn,
        lease_id=f"lhb:{trade_date}",
        holder=holder,
        trade_date=trade_date,
        ttl_seconds=300,
    )


def run_lhb_day(
    db_path: str,
    trade_date: str,
    *,
    holder: str,
    input_hash: str = "lhb-default",
    fns: dict[str, Callable[..., Any]] | None = None,
    source_status: str = "COMPLETE",
) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        got = try_lease(conn, trade_date=trade_date, holder=holder)
        if not got:
            return {"status": "LEASE_HELD", "trade_date": trade_date, "holder": holder}
    dag = build_lhb_dag(fns)
    runner = SchedulerRunner(db_path, dag)
    result = runner.run_day(
        trade_date,
        mode=LHB_MODE,
        scope_type="GLOBAL",
        scope_id="lhb",
        input_hash=input_hash,
    )
    result["source_status"] = source_status
    result["confirmed_blocked"] = confirmed_blocked(source_status)
    result["main_dag_untouched"] = DAG_STEPS == FROZEN_MAIN_DAG_STEPS
    return result


def emit_quality_alert(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    source_status: str,
    dry_run: bool = True,
) -> dict[str, Any] | None:
    if source_status not in ("FETCH_FAILED", "DEGRADED"):
        return None
    return create_alert(
        conn,
        alert_type="DATA_QUALITY",
        trade_date=trade_date,
        payload={"source_status": source_status},
        severity="WARN",
        dry_run=dry_run,
    )
