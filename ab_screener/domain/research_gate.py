"""研究 fail-closed 与晋级门禁。"""
from __future__ import annotations

from typing import Any

from ab_screener.domain.errors import FailClosedError

FORBIDDEN_EDGE_PHRASES = (
    "已验证 edge",
    "已验证edge",
    "可下单参数",
    "实盘参数",
    "保证收益",
    "稳赚",
)


def assert_no_edge_claim(text: str, *, research_mode: str) -> None:
    if research_mode == "full":
        return
    low = text or ""
    for p in FORBIDDEN_EDGE_PHRASES:
        if p in low:
            raise FailClosedError(f"degraded 模式禁止表述: {p}")


def can_promote_profile(
    *,
    research_mode: str,
    oos_net_pf: float | None,
    oos_max_dd: float | None,
    oos_win_rate: float | None,
    beats_baseline: bool,
    min_oos_pf: float = 1.0,
    max_dd: float = 0.25,
    min_wr: float = 0.30,
) -> dict[str, Any]:
    """返回 promotion_checks；全部 True 才可 active。"""
    checks: dict[str, Any] = {
        "research_mode_full": research_mode == "full",
        "oos_net_pf_ok": oos_net_pf is not None and oos_net_pf >= min_oos_pf,
        "oos_dd_ok": oos_max_dd is not None and oos_max_dd <= max_dd,
        "oos_wr_ok": oos_win_rate is not None and oos_win_rate >= min_wr,
        "beats_baseline": beats_baseline,
    }
    checks["promotable"] = all(
        bool(checks[k]) for k in (
            "research_mode_full", "oos_net_pf_ok", "oos_dd_ok", "oos_wr_ok", "beats_baseline",
        )
    )
    if research_mode != "full":
        checks["block_reason"] = "research_mode!=full（fail-closed）"
    elif not checks["promotable"]:
        failed = [
            k for k in (
                "research_mode_full", "oos_net_pf_ok", "oos_dd_ok", "oos_wr_ok", "beats_baseline",
            )
            if not checks[k]
        ]
        checks["block_reason"] = "未通过: " + ",".join(failed)
    else:
        checks["block_reason"] = None
    return checks
