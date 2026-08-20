"""ENTRY 定义 v2：在 V1 之上发布的新增语义（MA60 过滤、回踩容忍、两步式箱体）。

契约：V1 语义永远不变（A_POOL_STRICT_NEXT_OPEN_V1）；v2 只在其上新增/收紧
信号判定，入场时序（next-open）保持一致。默认生产候选仍为 V1，V2 数据只读，
仅独立研究通过后才允许切换生产候选定义。
"""
from __future__ import annotations

from typing import Any

from ab_screener.domain.entry_definition import ENTRY_DEFINITION_ID as V1_ID
from ab_screener.domain.entry_definition import definition_snapshot as v1_snapshot

ENTRY_DEFINITION_ID = "A_POOL_STRICT_NEXT_OPEN_V2"
ENTRY_DEFINITION_VERSION = "v2"
ENTRY_TIMING = "next_open"  # 与 V1 一致（突破日下一交易日开盘）
SIGNAL_ENGINE = "signals.detect_accumulation_breakout"
SIGNAL_PROFILE = "strict"

# v2 相对 V1 的语义增量（2026-08-16 突破逻辑 v2 的契约化）
V2_SEMANTIC_DELTAS = {
    "box_search": "two_step_breakout_first",  # 先找突破日，再在其前找箱体（箱体不含突破日）
    "position_guard": "full_window_based_fail_closed",  # 位置护栏基于完整窗口，历史不足拒绝
    "pullbacks_allowed": 0,  # 突破后收盘跌破箱体上沿允许次数（strict）
    "relaxed_pullbacks_allowed": 1,
    "require_ma60": True,  # 突破后收盘须站上 MA60（strict）
    "ma60_pullback_tolerance": 0.005,
}


def definition_snapshot() -> dict[str, Any]:
    """V2 快照 = V1 快照 + 语义增量 + 独立 ID（消费方可逐字段 diff）。"""
    base = v1_snapshot()
    base["id"] = ENTRY_DEFINITION_ID
    base["version"] = ENTRY_DEFINITION_VERSION
    base["base_on"] = V1_ID
    base["semantic_deltas"] = dict(V2_SEMANTIC_DELTAS)
    return base


def registry_entry() -> dict[str, Any]:
    """注册表条目（ID → snapshot + 声明）。"""
    return {
        "id": ENTRY_DEFINITION_ID,
        "version": ENTRY_DEFINITION_VERSION,
        "timing": ENTRY_TIMING,
        "signal_engine": SIGNAL_ENGINE,
        "signal_profile": SIGNAL_PROFILE,
        "base_on": V1_ID,
        "snapshot": definition_snapshot(),
    }
