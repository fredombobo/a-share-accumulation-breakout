"""策略插件注册表（P4.1）。

- `strategy_registry` 恰好注册 6 个 selection plugin（测试断言）。
- `regime_overlay_registry` 单独注册防守 overlay；overlay 不实现 SignalObservation producer。
- 一个插件异常被隔离（调用方捕获），其他插件继续。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ab_screener.strategies.contracts import SignalObservation, StrategySpec

DetectFn = Callable[..., list[SignalObservation]]

_SELECTION: dict[str, dict[str, Any]] = {}
_REGIME_OVERLAYS: dict[str, dict[str, Any]] = {}


class StrategyRegistryError(RuntimeError):
    """注册表错误（重复注册/未知策略，fail-closed）。"""


def register_selection_plugin(
    spec: StrategySpec, detect_fn: DetectFn
) -> None:
    """注册 selection 插件（幂等：同 id 重复注册拒绝）。"""
    if spec.strategy_definition_id in _SELECTION:
        raise StrategyRegistryError(
            f"重复注册 selection 插件: {spec.strategy_definition_id}"
        )
    _SELECTION[spec.strategy_definition_id] = {"spec": spec, "detect": detect_fn}


def register_regime_overlay(
    overlay_id: str, spec: StrategySpec, evaluate_fn: Callable[..., Any]
) -> None:
    """注册防守 overlay（独立 registry，不进入 selection）。"""
    if overlay_id in _REGIME_OVERLAYS:
        raise StrategyRegistryError(f"重复注册 regime overlay: {overlay_id}")
    _REGIME_OVERLAYS[overlay_id] = {"spec": spec, "evaluate": evaluate_fn}


def selection_plugins() -> dict[str, dict[str, Any]]:
    """六 selection 插件（id → {spec, detect}）。"""
    return dict(_SELECTION)


def selection_plugin_ids() -> list[str]:
    return sorted(_SELECTION)


def regime_overlays() -> dict[str, dict[str, Any]]:
    return dict(_REGIME_OVERLAYS)


def resolve_selection(strategy_definition_id: str) -> dict[str, Any]:
    if strategy_definition_id not in _SELECTION:
        raise StrategyRegistryError(
            f"未知 selection 插件: {strategy_definition_id}（已注册: {selection_plugin_ids()}）"
        )
    return _SELECTION[strategy_definition_id]


def require_six_selection_plugins() -> None:
    """契约：strategy_registry 必须恰好注册六个 selection plugin。"""
    if len(_SELECTION) != 6:
        raise StrategyRegistryError(
            f"selection 插件数 {len(_SELECTION)} ≠ 6（契约要求六形态）"
        )


def run_all_selection_plugins(
    bars: Any,
    *,
    ts_code: str,
    snapshot_id: str,
    input_hash: str,
    configs: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """逐插件检测并隔离异常：一个插件抛错不影响其他插件。"""
    configs = configs or {}
    results: dict[str, Any] = {}
    for plugin_id, entry in _SELECTION.items():
        try:
            cfg = configs.get(plugin_id)
            observations = entry["detect"](
                bars, cfg,
                ts_code=ts_code, snapshot_id=snapshot_id, input_hash=input_hash,
            )
            results[plugin_id] = observations
        except Exception as exc:  # noqa: BLE001
            results[plugin_id] = {"error": f"{type(exc).__name__}: {exc}"}
    return results
