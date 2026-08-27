"""v2 七闸门就绪状态评估（readiness gate）。

契约（acceptance 七闸门）：D 数据 / R 研究 / S 语义 / P 性能 / L 账本 /
O 运维 / G 总验收。硬门（D/S/P/L/O/G）任一失败 → BLOCKED；
仅 R（研究）未通过且其它全 PASS → ENGINEERING_READY_RESEARCH_BLOCKED。

本模块只做「状态判定」纯逻辑；证据输入由采集/验收流程提供。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GATES = ("D", "R", "S", "P", "L", "O", "G")

STATUS_BLOCKED = "BLOCKED"
STATUS_ENGINEERING_READY_RESEARCH_BLOCKED = "ENGINEERING_READY_RESEARCH_BLOCKED"
STATUS_PERSONAL_INSTITUTIONAL_READY = "PERSONAL_INSTITUTIONAL_READY"


@dataclass(frozen=True)
class ReadinessInput:
    """每个闸门的评估输入。pass=True 表示该闸门证据通过。"""

    gate_results: dict[str, bool] = field(default_factory=dict)
    worktree_clean: bool = True
    identity_matches: bool = True

    def __post_init__(self) -> None:
        missing = set(GATES) - set(self.gate_results)
        if missing:
            raise ValueError(f"闸门输入缺失: {sorted(missing)}（必须提供全部七闸门）")


def evaluate_readiness(ri: ReadinessInput) -> dict[str, Any]:
    """返回 {status, reason, per_gate, blocked_gates}。"""
    per_gate = {gate: bool(ri.gate_results[gate]) for gate in GATES}
    blocked = [g for g in GATES if not per_gate[g]]

    reasons: list[str] = []
    if not ri.worktree_clean:
        blocked.insert(0, "WORKTREE")
        reasons.append("WORKTREE_DIRTY")
    if not ri.identity_matches:
        blocked.insert(0, "IDENTITY")
        reasons.append("IDENTITY_MISMATCH")

    non_research_fail = any(not per_gate[g] for g in GATES if g != "R")
    research_fail_only = (not per_gate["R"]) and not non_research_fail

    # Identity outranks every gate verdict.  A dirty checkout or evidence from
    # another code/config/data identity can never be described as engineering
    # ready, even when R happens to be the only false business gate.
    if not ri.worktree_clean or not ri.identity_matches:
        status = STATUS_BLOCKED
    elif research_fail_only:
        status = STATUS_ENGINEERING_READY_RESEARCH_BLOCKED
        reasons.append("仅研究闸门未通过（工程就绪、研究阻断）")
    elif blocked:
        status = STATUS_BLOCKED
        if not reasons:
            reasons.append(f"硬门失败: {', '.join(blocked)}")
    else:
        status = STATUS_PERSONAL_INSTITUTIONAL_READY
        reasons.append("七闸门全部 PASS")

    return {
        "status": status,
        "reasons": reasons,
        "per_gate": per_gate,
        "blocked_gates": [g for g in GATES if not per_gate[g]],
        "identity_blockers": [
            code for code in ("WORKTREE_DIRTY" if not ri.worktree_clean else None,
                              "IDENTITY_MISMATCH" if not ri.identity_matches else None)
            if code is not None
        ],
    }
