"""持久每日 DAG（P6.1）：唯一 DAG 顺序 + 幂等 + attempt 保留 + 崩溃续跑。

- 幂等键：trade_date + step_name + scope_type + scope_id + input_hash；相同键最多成功一次。
- max_attempts=3（含首次）；保留每次 attempt。
- 上游 FAIL 阻断依赖步骤；人工补跑不能绕过数据/研究/风险/对账门禁（mode=HISTORICAL_REPLAY 显式）。
- 应用重启自动 catch-up 最近未完成交易日。
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ab_screener.domain.data_point import canonical_json

# 唯一 DAG 顺序（计划 P6.1 冻结）
DAG_STEPS: tuple[str, ...] = (
    "calendar_lease", "sync", "pit_gate",
    "instrument_corp_action_gate", "open_fill_replay",
    "close_valuation_settle", "market_breadth", "close_scan",
    "signal_observe_lifecycle", "outcome_backfill", "alerts_drafts",
    "daily_manifest", "backup_verify",
)

MAX_ATTEMPTS = 3
SCOPE_TYPES = ("GLOBAL", "ACCOUNT", "PROFILE")


class DagError(ValueError):
    """DAG 输入非法（fail-closed）。"""


def idempotency_key(
    trade_date: str, step_name: str, scope_type: str, scope_id: str, input_hash: str
) -> str:
    if scope_type not in SCOPE_TYPES:
        raise DagError(f"非法 scope_type: {scope_type}")
    blob = canonical_json({
        "trade_date": trade_date, "step": step_name,
        "scope_type": scope_type, "scope_id": scope_id, "input": input_hash,
    })
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class StepSpec:
    name: str
    scope_type: str
    scope_id: str
    fn: Callable[..., Any]
    depends_on: tuple[str, ...] = ()


class DailyDag:
    """按唯一顺序执行步骤；崩溃续跑从最后完成步骤开始。"""

    def __init__(self, steps: list[StepSpec], max_attempts: int = MAX_ATTEMPTS):
        if not steps:
            raise DagError("DAG 至少需要一个步骤")
        names = [s.name for s in steps]
        if len(set(names)) != len(names):
            raise DagError("步骤名不能重复")
        self.steps = steps
        self.max_attempts = max_attempts

    def order(self) -> list[str]:
        return [s.name for s in self.steps]

    def dependencies_of(self, name: str) -> tuple[str, ...]:
        for s in self.steps:
            if s.name == name:
                return s.depends_on
        raise DagError(f"未知步骤: {name}")
