"""六形态策略 API（P7.1）：registry 与研究状态（只读）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ab_screener.strategies.contracts import strategy_hash
from ab_screener.strategies.registry import (
    resolve_selection,
    selection_plugins,
)

router = APIRouter(prefix="/api/v2/strategies", tags=["strategies"])


@router.get("")
def list_strategies() -> dict[str, Any]:
    """六形态 registry 与研究状态。"""
    plugins = selection_plugins()
    return {
        "strategies": [
            {
                "strategy_definition_id": sid,
                "version": entry["spec"].version,
                "research_status": entry["spec"].research_status,
                "economic_assumption": entry["spec"].economic_assumption,
                "failure_conditions": entry["spec"].failure_conditions,
                "config_path": entry["spec"].config_path,
                "strategy_hash": strategy_hash(entry["spec"]),
            }
            for sid, entry in sorted(plugins.items())
        ],
        "count": len(plugins),
    }


@router.get("/{strategy_id}/versions")
def strategy_versions(strategy_id: str) -> dict[str, Any]:
    """插件版本、假设和失效条件。"""
    try:
        entry = resolve_selection(strategy_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    spec = entry["spec"]
    return {
        "strategy_definition_id": spec.strategy_definition_id,
        "version": spec.version,
        "economic_assumption": spec.economic_assumption,
        "failure_conditions": spec.failure_conditions,
        "pit_test": spec.pit_test,
        "research_status": spec.research_status,
        "strategy_hash": strategy_hash(spec),
    }
