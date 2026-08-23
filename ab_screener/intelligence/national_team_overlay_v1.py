"""国家队/机构资金信息增强只读覆盖层 v1（V2R-N）。

范围：解释、标记与研究排序；绝不进入 A/B 池资格、仓位权重或订单。
PIT 纪律：历史决策只读 `available_at <= decision_at` 的观测；
`available_at > decision_at` → 结构化 INSUFFICIENT（看不到未来记录）。
供应商不可用 / 无 source / 权限不足 / 字段缺失 → 结构化 INSUFFICIENT。

本模块为纯领域逻辑：不触网、不读库、不写任何账本；注册在独立 intelligence 命名空间，
不进入 `ab_screener.regimes.registry` 的开仓许可 registry（只读边界）。
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime

from ab_screener.data.adapters.ntm_client import (
    NationalTeamObservation,
    OverlayInsufficient,
    parse_ts_aware,
)
from ab_screener.domain.data_point import canonical_json, normalize_ts
from ab_screener.strategies.contracts import StrategySpec

NATIONAL_TEAM_OVERLAY_ID = "national_team_overlay_v1"
OVERLAY_VERSION = "v1"
OVERLAY_DISCLAIMER = "研究情报，不是买卖指令，不进入 A/B 池，不改变资格、仓位或订单。"

SPEC = StrategySpec(
    strategy_definition_id=NATIONAL_TEAM_OVERLAY_ID,
    version=OVERLAY_VERSION,
    economic_assumption=(
        "国家队/机构资金（NTM 快照）共振信息用于解释与研究排序；"
        "危险共振/机会共振只作标记，不改变 A/B 资格、仓位或订单"
    ),
    failure_conditions=(
        "NTM 快照缺失/过期被误判为信号；快照滞后导致未来函数；"
        "覆盖层被误接进资格/仓位/订单路径"
    ),
    pit_test="仅读取 available_at <= decision_at 的观测；available_at > decision_at → INSUFFICIENT",
    golden_fixture="tests/test_evaluate_overlays.py::frozen_parity_case（冻结行情+冻结扫描输入）",
    config_path="configs/intelligence/national_team_overlay_v1.yaml",
)


def _parse_decision_at(decision_at: str) -> datetime | None:
    """决策时点必须带时区且可解析；否则 None（fail-closed）。"""
    try:
        return parse_ts_aware(decision_at)
    except ValueError:
        return None


def evaluate(
    record: NationalTeamObservation | None,
    *,
    decision_at: str,
) -> NationalTeamObservation | OverlayInsufficient:
    """PIT 求值：记录在 decision_at 是否可读。

    - record 为 None（供应商不可用）→ INSUFFICIENT(vendor_unavailable)。
    - decision_at 无时区/不可解析 → INSUFFICIENT(invalid_decision_at)。
    - available_at > decision_at → INSUFFICIENT(future_information)，看不到未来记录。
    - 否则返回可读观测记录。
    """
    if record is None:
        return OverlayInsufficient(reason="vendor_unavailable", detail="NTM 快照未配置/不可用")
    if _parse_decision_at(decision_at) is None:
        return OverlayInsufficient(
            reason="invalid_decision_at",
            detail=f"decision_at 必须为带时区时间戳: {decision_at!r}",
        )
    if normalize_ts(record.available_at) > normalize_ts(decision_at):
        return OverlayInsufficient(
            reason="future_information",
            detail=(
                f"available_at={record.available_at} > decision_at={decision_at}"
                "（历史决策不得读取未来记录）"
            ),
            decision_at=decision_at,
        )
    return record


def records_for_decision(
    records: Sequence[NationalTeamObservation],
    decision_at: str,
) -> tuple[NationalTeamObservation, ...]:
    """历史读取：只返回 available_at <= decision_at 的记录，按 available_at 升序。

    决策时点无效（无时区/不可解析）→ 空元组（fail-closed，读不到任何记录）。
    """
    if _parse_decision_at(decision_at) is None:
        return ()
    normalized_decision = normalize_ts(decision_at)
    visible = [
        r for r in records if normalize_ts(r.available_at) <= normalized_decision
    ]
    return tuple(sorted(visible, key=lambda r: normalize_ts(r.available_at)))


def observation_hash(record: NationalTeamObservation) -> str:
    """观测规范化指纹（SHA-256 前 16 位）：同观测 → 同指纹，重放可审计。"""
    return hashlib.sha256(canonical_json(record.to_dict()).encode("utf-8")).hexdigest()[:16]
