"""信息覆盖层汇总执行（V2R-N）：只读注释，绝不进入资格/仓位/订单。

契约：
- `evaluate_overlays` 纯函数：按 decision_at 对覆盖层观测做 PIT 求值，
  输出 `OverlayEvaluationResult`（观测 + 结构化 INSUFFICIENT）。
- `annotate_decision` 只把观测作为注释附加到决策**副本**；A/B 资格、
  目标仓位、订单字段保持逐字节一致。
- 供应商不可用/解析失败 → INSUFFICIENT（不伪造、不抛无结构异常）。
- 本模块不触网、不读库、不写任何账本。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ab_screener.data.adapters.ntm_client import (
    NationalTeamObservation,
    OverlayInsufficient,
    parse_ntm_snapshot,
)
from ab_screener.intelligence.national_team_overlay_v1 import (
    NATIONAL_TEAM_OVERLAY_ID,
    OVERLAY_DISCLAIMER,
)
from ab_screener.intelligence.national_team_overlay_v1 import (
    evaluate as evaluate_national_team,
)


@dataclass(frozen=True)
class OverlayEvaluationResult:
    """覆盖层求值结果：只读注释，不是交易输入。

    - observations：可读观测（available_at <= decision_at）。
    - insufficiencies：结构化 INSUFFICIENT（供应商不可用/权限/字段/未来信息）。
    """

    status: str
    decision_at: str
    overlay_ids: tuple[str, ...]
    observations: tuple[NationalTeamObservation, ...] = field(default_factory=tuple)
    insufficiencies: tuple[OverlayInsufficient, ...] = field(default_factory=tuple)
    not_a_pool: bool = True
    research_only: bool = True
    disclaimer: str = OVERLAY_DISCLAIMER
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "decision_at": self.decision_at,
            "overlay_ids": list(self.overlay_ids),
            "observations": [o.to_dict() for o in self.observations],
            "insufficiencies": [i.to_dict() for i in self.insufficiencies],
            "not_a_pool": self.not_a_pool,
            "research_only": self.research_only,
            "disclaimer": self.disclaimer,
            "summary": self.summary,
        }


def _evaluate_national_team_overlay(
    overlay_raw: dict[str, Any] | None,
    *,
    decision_at: str,
    ingested_at: str | None,
) -> tuple[
    tuple[NationalTeamObservation, ...],
    tuple[OverlayInsufficient, ...],
]:
    parsed = parse_ntm_snapshot(overlay_raw, ingested_at=ingested_at)
    if isinstance(parsed, OverlayInsufficient):
        return (), (parsed,)
    decision = evaluate_national_team(parsed, decision_at=decision_at)
    if isinstance(decision, OverlayInsufficient):
        return (), (decision,)
    return (decision,), ()


def evaluate_overlays(
    overlay_raw: dict[str, Any] | None,
    *,
    decision_at: str,
    ingested_at: str | None = None,
    overlays: tuple[str, ...] = (NATIONAL_TEAM_OVERLAY_ID,),
) -> OverlayEvaluationResult:
    """对覆盖层集按 decision_at 求值，返回只读注释结果。

    顺序无关、fail-closed：任一覆盖层不可读 → 该覆盖层记 INSUFFICIENT，
    其余覆盖层照常；任何情况下不进入资格/仓位/订单路径。
    """
    observations: list[NationalTeamObservation] = []
    insufficiencies: list[OverlayInsufficient] = []
    for overlay_id in overlays:
        if overlay_id == NATIONAL_TEAM_OVERLAY_ID:
            obs, ins = _evaluate_national_team_overlay(
                overlay_raw, decision_at=decision_at, ingested_at=ingested_at
            )
            observations.extend(obs)
            insufficiencies.extend(ins)
        else:
            insufficiencies.append(
                OverlayInsufficient(
                    reason="unknown_overlay",
                    detail=f"未知覆盖层: {overlay_id}",
                    decision_at=decision_at,
                )
            )
    status = "PASS" if observations and not insufficiencies else "INSUFFICIENT"
    summary = (
        f"{len(observations)} 条可读观测；{len(insufficiencies)} 条 INSUFFICIENT"
        "（覆盖层只注释，不改变资格/仓位/订单）"
    )
    return OverlayEvaluationResult(
        status=status,
        decision_at=decision_at,
        overlay_ids=tuple(overlays),
        observations=tuple(observations),
        insufficiencies=tuple(insufficiencies),
        summary=summary,
    )


def annotate_decision(
    decision: Mapping[str, Any],
    overlay_result: OverlayEvaluationResult,
) -> dict[str, Any]:
    """把覆盖层观测作为注释附加到决策副本。

    - 原决策字段不做任何修改（副本逐字节一致）。
    - 只新增 `annotations`（观测 dict 列表）与 `disclaimer` 两个注释键。
    """
    annotated = dict(decision)
    annotated["annotations"] = [
        observation.to_dict() for observation in overlay_result.observations
    ]
    annotated["disclaimer"] = overlay_result.disclaimer
    return annotated
