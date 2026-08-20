"""DSL 适配器（P4.1）：logic_platform 经本 adapter 使用同一策略契约。

不再建立第二套生产状态机：DSL 定义 → StrategySpec 契约。
"""
from __future__ import annotations

from typing import Any

from ab_screener.strategies.contracts import StrategySpec


def dsl_strategy_to_spec(dsl_definition: dict[str, Any]) -> StrategySpec:
    """把 logic_platform 的 DSL 定义适配为 StrategySpec。

    dsl_definition 必须含 strategy_definition_id/version/description/failure；
    缺字段 → 抛错（fail-closed，不静默降级）。
    """
    required = ("strategy_definition_id", "version")
    missing = [k for k in required if not dsl_definition.get(k)]
    if missing:
        raise ValueError(f"DSL 定义缺少字段: {missing}")
    description = dsl_definition.get("description") or dsl_definition.get("assumption")
    failure = dsl_definition.get("failure") or dsl_definition.get("failure_conditions")
    if not description or not failure:
        raise ValueError("DSL 定义缺少 description/failure（经济假设与失效条件必须显式声明）")
    return StrategySpec(
        strategy_definition_id=str(dsl_definition["strategy_definition_id"]),
        version=str(dsl_definition["version"]),
        economic_assumption=str(description),
        failure_conditions=str(failure),
        pit_test=str(dsl_definition.get("pit_test") or "防未来函数由 DSL 编译期检查"),
        golden_fixture=str(dsl_definition.get("golden_fixture") or "（DSL 适配，未指定）"),
        research_status=str(dsl_definition.get("research_status") or "EXPERIMENTAL"),
        config_path=str(dsl_definition.get("config_path") or ""),
    )
