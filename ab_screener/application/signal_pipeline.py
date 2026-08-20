"""信号管线（P4.2）：插件观察 → 不可变信号落库（幂等）。

管线调用六插件 `run_all_selection_plugins` 得到观察，逐个 `save_observation`
落库（同 strategy/snapshot/input hash 重跑幂等）。防守 overlay 不产生观察。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ab_screener.data.signal_repository import save_observation
from ab_screener.strategies.contracts import SignalObservation
from ab_screener.strategies.registry import run_all_selection_plugins


class SignalPipelineError(RuntimeError):
    """管线错误（fail-closed）。"""


def run_signal_pipeline(
    conn: sqlite3.Connection,
    *,
    bars: Any,
    ts_code: str,
    snapshot_id: str,
    input_hash: str,
    configs: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """执行全部 selection 插件并落库观察；返回 {saved, errors}。"""
    results = run_all_selection_plugins(
        bars, ts_code=ts_code, snapshot_id=snapshot_id, input_hash=input_hash,
        configs=configs,
    )
    saved: list[str] = []
    errors: dict[str, str] = {}
    for plugin_id, value in results.items():
        if isinstance(value, dict) and "error" in value:
            errors[plugin_id] = value["error"]
            continue
        for obs in value:
            if not isinstance(obs, SignalObservation):
                raise SignalPipelineError(
                    f"插件 {plugin_id} 返回非 SignalObservation: {type(obs).__name__}"
                )
            saved.append(save_observation(conn, obs))
    return {
        "plugins_run": list(results),
        "saved_observation_ids": saved,
        "saved_count": len(saved),
        "errors": errors,
    }
