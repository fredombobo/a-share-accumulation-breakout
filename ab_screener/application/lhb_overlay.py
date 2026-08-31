"""龙虎榜 research overlay（T12）。默认关闭；打开也不改 A 池分数/仓位/订单。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ab_screener.application.lhb_signal_engine import load_policy

OVERLAY_ID = "lhb-research-overlay-v1"
POOL_SCORE_KEYS = ("score", "rank", "position", "weight", "tradeable", "pool")


def overlay_enabled(policy: dict[str, Any] | None = None) -> bool:
    cfg = policy if policy is not None else load_policy()
    return bool(cfg.get("overlay_enabled", False))


def attach_lhb_overlay(
    candidates: list[dict[str, Any]],
    signals_by_code: dict[str, dict[str, Any]] | None = None,
    *,
    enabled: bool | None = None,
    research_status: str = "RESEARCH_BLOCKED",
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """关闭时原样返回。打开时只追加解释字段，不改分数/排序/仓位。"""
    on = overlay_enabled(policy) if enabled is None else enabled
    if not on:
        return deepcopy(candidates)
    signals_by_code = signals_by_code or {}
    out: list[dict[str, Any]] = []
    for row in candidates:
        item = deepcopy(row)
        code = str(item.get("ts_code") or "")
        sig = signals_by_code.get(code)
        item["lhb_research"] = {
            "overlay_id": OVERLAY_ID,
            "research_only": True,
            "research_status": research_status,
            "does_not_change_pool": True,
            "generates_orders": False,
            "signal": None
            if sig is None
            else {
                "status": sig.get("status"),
                "vetoes": sig.get("vetoes") or [],
                "policy_version": sig.get("policy_version"),
                "earliest_executable_at": sig.get("earliest_executable_at"),
            },
        }
        out.append(item)
    return out


def pool_fingerprint(candidates: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    keys = ("ts_code",) + POOL_SCORE_KEYS
    out: list[tuple[Any, ...]] = []
    for row in candidates:
        out.append(tuple(row.get(k) for k in keys))
    return out


def overlay_changed_pool(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> bool:
    return pool_fingerprint(before) != pool_fingerprint(after)
