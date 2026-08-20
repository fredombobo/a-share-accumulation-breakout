"""扫描漏斗（P4.2）：阶段集合显式声明（禁止写死阶段数量）+ 集合守恒。

- 阶段列表由调用方显式传入（不可写死 N 个阶段）；`ScanFunnel` 只按传入阶段执行。
- A/B 分支验收使用集合守恒：输入标的集合 == 各分支输出并集。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

StageFn = Callable[[Any, str], Any]


class FunnelError(ValueError):
    """漏斗输入非法（fail-closed）。"""


@dataclass(frozen=True)
class FunnelStageResult:
    stage_id: str
    output: Any
    ok: bool
    error: str = ""


class ScanFunnel:
    """显式阶段列表的扫描漏斗；阶段异常被隔离，其余阶段继续。"""

    def __init__(self, stages: list[str]):
        if not stages:
            raise FunnelError("漏斗至少需要一个阶段")
        if len(set(stages)) != len(stages):
            raise FunnelError("阶段 id 不能重复")
        self.stages = list(stages)

    def run(
        self,
        initial: Any,
        stage_fns: dict[str, StageFn],
    ) -> dict[str, FunnelStageResult]:
        """按阶段顺序执行；某阶段抛错 → 记录 error，其余阶段继续。"""
        results: dict[str, FunnelStageResult] = {}
        current = initial
        for stage in self.stages:
            fn = stage_fns.get(stage)
            if fn is None:
                raise FunnelError(f"缺少阶段实现: {stage}")
            try:
                current = fn(current, stage)
                results[stage] = FunnelStageResult(stage, current, True)
            except Exception as exc:  # noqa: BLE001
                results[stage] = FunnelStageResult(stage, None, False, str(exc))
        return results


def assert_set_conservation(inputs: set[str], branches: list[set[str]]) -> None:
    """A/B 分支集合守恒：输入 == 各分支输出并集（验收）。"""
    union = set().union(*branches) if branches else set()
    if inputs != union:
        raise FunnelError(
            f"集合不守恒: 输入 {len(inputs)} ≠ 分支并集 {len(union)}"
            f"（缺 {inputs - union} / 多 {union - inputs}）"
        )
