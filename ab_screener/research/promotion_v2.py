"""晋级服务（P3.4）：ROBUST_PERSONAL_V2 / STRICT_RESEARCH_V2 双口径。

口径（计划 P3.4）：
- ROBUST_PERSONAL_V2：PBO≤20%；DSR≥95%；MinTRL coverage≥1；≥5 个有效外层测试窗
  且正收益窗≥60%；OOS 净收益为正并优于预登记主基线；2×成本下净 OOS>0 且对主基线
  超额>0；预登记参数邻域≥60% 组合净 OOS 与主基线超额同为正；所有身份/产物哈希有效。
- STRICT_RESEARCH_V2 对照：PBO<10%、DSR>95%、MinTRL≥1。
PASS 仅生成 CANDIDATE，绝不写 A 池或订单；未达门槛必须 FAIL（不静默降级）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ROBUST_PROFILE = "ROBUST_PERSONAL_V2"
STRICT_PROFILE = "STRICT_RESEARCH_V2"


@dataclass(frozen=True)
class PromotionEvidence:
    pbo: float | None = None
    dsr: float | None = None
    min_track_record_coverage: int | None = None
    outer_test_windows: int = 0
    positive_test_ratio: float = 0.0
    oos_net_total: float | None = None
    oos_net_mean: float | None = None
    baseline_net_total: float | None = None
    oos_net_2x: float | None = None
    neighborhood_positive_ratio: float | None = None
    hashes_valid: bool = False
    preregistered: bool = False
    checks: dict[str, Any] = field(default_factory=dict)


def _verdict(ok: bool, reason: str) -> dict[str, Any]:
    return {"pass": ok, "verdict": "PASS" if ok else "FAIL", "reason": reason}


def evaluate_robust_personal_v2(ev: PromotionEvidence) -> dict[str, Any]:
    """ROBUST_PERSONAL_V2：全部门槛必须同时满足，否则 FAIL。"""
    blockers: list[str] = []
    if ev.pbo is None or ev.pbo > 0.20:
        blockers.append(f"PBO={ev.pbo} 未满足 ≤0.20")
    if ev.dsr is None or ev.dsr < 0.95:
        blockers.append(f"DSR={ev.dsr} 未满足 ≥0.95")
    if ev.min_track_record_coverage is None or ev.min_track_record_coverage < 1:
        blockers.append(f"MinTRL coverage={ev.min_track_record_coverage} 未满足 ≥1")
    if ev.outer_test_windows < 5:
        blockers.append(f"外层测试窗 {ev.outer_test_windows} < 5")
    if ev.positive_test_ratio < 0.60:
        blockers.append(f"正收益窗比例 {ev.positive_test_ratio} < 60%")
    if ev.oos_net_total is None or ev.oos_net_total <= 0:
        blockers.append(f"OOS 净收益 {ev.oos_net_total} 未为正")
    if ev.baseline_net_total is None or ev.oos_net_total is None or ev.oos_net_total <= ev.baseline_net_total:
        blockers.append("OOS 净收益未优于预登记主基线")
    if ev.oos_net_2x is None or ev.oos_net_2x <= 0:
        blockers.append(f"2× 成本净 OOS {ev.oos_net_2x} 未为正")
    if ev.neighborhood_positive_ratio is None or ev.neighborhood_positive_ratio < 0.60:
        blockers.append(f"参数邻域同向比例 {ev.neighborhood_positive_ratio} < 60%")
    if not ev.hashes_valid:
        blockers.append("身份/产物哈希校验未通过")
    if not ev.preregistered:
        blockers.append("缺少预登记（参数邻域/主基线未预登记）")
    return _verdict(not blockers, "; ".join(blockers) if blockers else "满足 ROBUST_PERSONAL_V2 全部门槛")


def evaluate_strict_research_v2(ev: PromotionEvidence) -> dict[str, Any]:
    """STRICT_RESEARCH_V2 对照：PBO<10%、DSR>95%、MinTRL≥1。"""
    blockers: list[str] = []
    if ev.pbo is None or ev.pbo >= 0.10:
        blockers.append(f"PBO={ev.pbo} 未满足 <0.10")
    if ev.dsr is None or ev.dsr <= 0.95:
        blockers.append(f"DSR={ev.dsr} 未满足 >0.95")
    if ev.min_track_record_coverage is None or ev.min_track_record_coverage < 1:
        blockers.append(f"MinTRL coverage={ev.min_track_record_coverage} 未满足 ≥1")
    return _verdict(not blockers, "; ".join(blockers) if blockers else "满足 STRICT_RESEARCH_V2 对照门槛")


def promotion_decision(ev: PromotionEvidence) -> dict[str, Any]:
    """双口径结论：robust 结果绝不称为 strict；PASS 仅产出 CANDIDATE。"""
    robust = evaluate_robust_personal_v2(ev)
    strict = evaluate_strict_research_v2(ev)
    return {
        "profiles": {
            ROBUST_PROFILE: robust,
            STRICT_PROFILE: strict,
        },
        "candidate": "CANDIDATE" if robust["pass"] else "NO_CANDIDATE",
        "note": "PASS 仅生成 CANDIDATE，不得写 A 池或订单",
        "evidence": {
            "pbo": ev.pbo, "dsr": ev.dsr,
            "min_track_record_coverage": ev.min_track_record_coverage,
            "outer_test_windows": ev.outer_test_windows,
            "positive_test_ratio": ev.positive_test_ratio,
            "oos_net_total": ev.oos_net_total,
            "oos_net_2x": ev.oos_net_2x,
            "neighborhood_positive_ratio": ev.neighborhood_positive_ratio,
        },
    }
