"""regime overlay 注册表（P4.1）：单独注册防守 overlay，不进入 selection。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ab_screener.strategies.contracts import StrategySpec

_REGIME_OVERLAYS: dict[str, dict[str, Any]] = {}


class OverlayRegistryError(RuntimeError):
    """overlay 注册表错误（fail-closed）。"""


def register_regime_overlay(
    overlay_id: str, spec: StrategySpec, evaluate_fn: Callable[..., Any]
) -> None:
    if overlay_id in _REGIME_OVERLAYS:
        raise OverlayRegistryError(f"重复注册 regime overlay: {overlay_id}")
    _REGIME_OVERLAYS[overlay_id] = {"spec": spec, "evaluate": evaluate_fn}


def regime_overlays() -> dict[str, dict[str, Any]]:
    return dict(_REGIME_OVERLAYS)


def resolve_overlay(overlay_id: str) -> dict[str, Any]:
    if overlay_id not in _REGIME_OVERLAYS:
        raise OverlayRegistryError(f"未知 regime overlay: {overlay_id}")
    return _REGIME_OVERLAYS[overlay_id]
