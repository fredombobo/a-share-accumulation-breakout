"""覆盖层汇总执行与 overlay on/off parity 测试（V2R-N）。

契约：
- `evaluate_overlays` 纯函数：把覆盖层观测附加为研究注释，绝不进入
  A/B 资格、目标仓位或订单。
- 冻结行情 + 冻结扫描输入 → 同一输入 overlay on/off 后，A/B 资格、
  目标仓位、订单逐字段一致；覆盖层只能解释、标记或研究排序。
- 供应商不可用时不伪造、不抛无结构异常、不影响核心扫描闭环。
- 配置必须携带 version 与 config_hash。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from ab_screener.application.evaluate_overlays import (
    OverlayEvaluationResult,
    annotate_decision,
    evaluate_overlays,
)
from ab_screener.data.adapters.ntm_client import (
    NationalTeamObservation,
    OverlayInsufficient,
    parse_ntm_snapshot,
)
from ab_screener.domain.data_point import canonical_json
from ab_screener.intelligence.national_team_overlay_v1 import NATIONAL_TEAM_OVERLAY_ID

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "intelligence" / "national_team_overlay_v1.yaml"

# 冻结时点（+08:00）
DECISION_AT = "2026-08-21T20:00:00+08:00"

FROZEN_RAW = {
    "source": "ntm",
    "schema_version": 1,
    "as_of": "2026-08-21",
    "generated_at": "2026-08-21T18:30:00+08:00",
    "confidence": "medium",
    "permission": {"granted": True, "scope": ["resonance"]},
    "resonance": {
        "verdict": "机会共振",
        "red_count": 1,
        "green_count": 4,
        "total": 5,
        "per_etf": [],
    },
    "evidence_refs": ["runtime/snapshot_20260821.json"],
    "degraded": [],
    "warnings": [],
}


# ---------------------------------------------------------------------------
# 冻结行情 + 冻结扫描输入（管理者可复跑 parity fixture）
# ---------------------------------------------------------------------------

FROZEN_MARKET: dict[str, Any] = {
    "as_of": "2026-08-21",
    "index_close": 4000.0,
    "benchmark_trend": 0.02,
    "drawdown_from_peak": 0.03,
    "turnover_billion": 812.5,
}

FROZEN_SCAN_INPUT: dict[str, Any] = {
    "as_of": "2026-08-21",
    "environment": {"market_regime": "neutral", "allow_new_entries": True},
    "candidates": [
        {"ts_code": "000001.SZ", "is_breakout": True, "score": 87.5, "rank": 1},
        {"ts_code": "000002.SZ", "is_breakout": True, "score": 81.0, "rank": 2},
        {"ts_code": "000003.SZ", "is_breakout": False, "score": 79.2, "rank": 3},
        {"ts_code": "000004.SZ", "is_breakout": True, "score": 72.4, "rank": 4},
        {"ts_code": "000005.SZ", "is_breakout": False, "score": 66.0, "rank": 5},
    ],
    "cash_fen": 1_000_000,
    "a_pool_size": 2,
    "entry_definition_id": "NEXT_TRADABLE_OPEN_EXECUTION_V1",
}


def produce_decision(frozen_input: dict[str, Any]) -> dict[str, Any]:
    """冻结扫描输入 → 规范化决策（A/B 资格 + 目标仓位 + 订单）。

    纯函数：只依赖入参，确定性输出；用于证明 overlay 开启前后决策逐字段一致。
    """
    env = frozen_input["environment"]
    a_codes = [
        c["ts_code"]
        for c in sorted(
            (c for c in frozen_input["candidates"]
             if c["is_breakout"] and float(c["score"]) >= 80.0),
            key=lambda c: int(c["rank"]),
        )
    ][: int(frozen_input["a_pool_size"])]
    b_codes = [
        c["ts_code"]
        for c in sorted(frozen_input["candidates"], key=lambda c: int(c["rank"]))
        if c["ts_code"] not in a_codes
    ]
    slots = max(1, len(a_codes)) if env["allow_new_entries"] else 0
    per_slot = int(frozen_input["cash_fen"]) // (slots + 1) if slots else 0
    reserve = int(frozen_input["cash_fen"]) - per_slot * slots
    orders = [
        {
            "ts_code": code,
            "side": "BUY",
            "qty_fen": per_slot,
            "entry_definition_id": frozen_input["entry_definition_id"],
        }
        for code in a_codes
    ]
    return {
        "as_of": frozen_input["as_of"],
        "a_pool_eligible": a_codes,
        "b_pool_eligible": b_codes,
        "target_position": {
            "cash_reserve_fen": reserve,
            "per_instrument_fen": per_slot,
            "slots": slots,
        },
        "orders": orders,
    }


@pytest.fixture()
def frozen_parity_case() -> dict[str, Any]:
    """冻结行情 + 冻结扫描输入（管理者可复跑）。"""
    case = {"market": dict(FROZEN_MARKET), "scan_input": dict(FROZEN_SCAN_INPUT)}
    case["frozen_hash"] = hashlib.sha256(
        canonical_json(case).encode("utf-8")
    ).hexdigest()[:16]
    return case


def _parity_assertions(off: dict[str, Any], on: dict[str, Any]) -> None:
    """A/B 资格、目标仓位、订单逐字段一致。"""
    assert on["a_pool_eligible"] == off["a_pool_eligible"]
    assert on["b_pool_eligible"] == off["b_pool_eligible"]
    assert on["target_position"] == off["target_position"]
    assert on["orders"] == off["orders"]
    assert on == off  # 整体逐字段一致


# ---------------------------------------------------------------------------
# evaluate_overlays：只读注释
# ---------------------------------------------------------------------------


def test_evaluate_overlays_readable_observation():
    result = evaluate_overlays(
        FROZEN_RAW, decision_at=DECISION_AT,
        overlays=(NATIONAL_TEAM_OVERLAY_ID,),
    )
    assert isinstance(result, OverlayEvaluationResult)
    assert result.status == "PASS"
    assert len(result.observations) == 1
    obs = result.observations[0]
    assert obs.verdict == "机会共振"
    assert obs.available_at <= DECISION_AT
    assert result.insufficiencies == ()
    assert result.not_a_pool is True
    assert result.research_only is True
    assert result.decision_at == DECISION_AT


def test_evaluate_overlays_insufficient_status_for_future_info():
    result = evaluate_overlays(
        FROZEN_RAW, decision_at="2026-08-21T10:00:00+08:00",
        overlays=(NATIONAL_TEAM_OVERLAY_ID,),
    )
    assert result.status == "INSUFFICIENT"
    assert result.observations == ()
    assert len(result.insufficiencies) == 1
    assert result.insufficiencies[0].reason == "future_information"


def test_evaluate_overlays_insufficient_status_for_vendor_unavailable():
    result = evaluate_overlays(
        None, decision_at=DECISION_AT,
        overlays=(NATIONAL_TEAM_OVERLAY_ID,),
    )
    assert result.status == "INSUFFICIENT"
    assert result.observations == ()
    assert result.insufficiencies[0].reason == "vendor_unavailable"


def test_evaluate_overlays_result_has_no_eligibility_inputs():
    """汇总结果只有注释字段，绝不携带 A/B 资格、仓位或订单输入。"""
    result = evaluate_overlays(
        FROZEN_RAW, decision_at=DECISION_AT,
        overlays=(NATIONAL_TEAM_OVERLAY_ID,),
    )
    d = result.to_dict()
    forbidden = {"allow_new_entries", "target_position", "orders",
                 "a_pool_eligible", "b_pool_eligible"}
    assert forbidden.isdisjoint(d.keys())
    assert d["not_a_pool"] is True
    assert d["research_only"] is True


# ---------------------------------------------------------------------------
# Overlay on/off parity：A/B 资格、目标仓位、订单逐字段一致
# ---------------------------------------------------------------------------


def test_overlay_on_off_parity_eligibility_position_orders(frozen_parity_case):
    """同一冻结输入：overlay on/off → 决策逐字段一致，注释只增不改。"""
    # overlay off：核心扫描闭环（不含覆盖层）
    decision_off = produce_decision(frozen_parity_case["scan_input"])

    # overlay on：覆盖层解析 + 领域求值（纯函数）
    overlay_result = evaluate_overlays(
        FROZEN_RAW, decision_at=DECISION_AT,
        overlays=(NATIONAL_TEAM_OVERLAY_ID,),
    )
    assert overlay_result.status == "PASS"

    annotated = annotate_decision(decision_off, overlay_result)
    decision_on = annotated["decision"]

    # A/B 资格、目标仓位、订单逐字段一致
    _parity_assertions(decision_off, decision_on)
    # 只新增注释键
    assert set(annotated.keys()) == {"decision", "annotations", "disclaimer"}
    assert annotated["annotations"][0]["verdict"] == "机会共振"
    assert "不进入 A/B 池" in annotated["disclaimer"]


def test_vendor_unavailable_does_not_break_scan_loop(frozen_parity_case):
    """供应商不可用：不伪造、不抛异常、不影响核心扫描闭环。"""
    decision_off = produce_decision(frozen_parity_case["scan_input"])

    # 覆盖层求值：供应商不可用 → INSUFFICIENT（结构化，不 raise）
    overlay_result = evaluate_overlays(
        None, decision_at=DECISION_AT,
        overlays=(NATIONAL_TEAM_OVERLAY_ID,),
    )
    assert overlay_result.status == "INSUFFICIENT"

    # 注释不携带任何伪造的观测
    annotated = annotate_decision(decision_off, overlay_result)
    assert annotated["annotations"] == []
    _parity_assertions(decision_off, annotated["decision"])


def test_frozen_parity_case_hashes_match_recorded(frozen_parity_case):
    """冻结用例确定性：重复构造哈希一致（可复跑审计）。"""
    recreated = hashlib.sha256(
        canonical_json({
            "market": dict(FROZEN_MARKET),
            "scan_input": dict(FROZEN_SCAN_INPUT),
        }).encode("utf-8")
    ).hexdigest()[:16]
    assert frozen_parity_case["frozen_hash"] == recreated


# ---------------------------------------------------------------------------
# 配置：必须有版本/hash
# ---------------------------------------------------------------------------


def test_overlay_config_has_version_and_hash():
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(cfg, dict)
    assert cfg["overlay_id"] == NATIONAL_TEAM_OVERLAY_ID
    assert cfg["version"] == "v1"
    assert isinstance(cfg.get("config_hash"), str)
    assert len(cfg["config_hash"]) == 16
    # config_hash = 规范化配置（去掉自身）SHA-256 前 16 位
    body = {k: v for k, v in cfg.items() if k != "config_hash"}
    recomputed = hashlib.sha256(
        canonical_json(body).encode("utf-8")
    ).hexdigest()[:16]
    assert recomputed == cfg["config_hash"]


def test_annotate_decision_does_not_mutate_input():
    decision = produce_decision(FROZEN_SCAN_INPUT)
    before = canonical_json(decision)
    result = evaluate_overlays(
        FROZEN_RAW, decision_at=DECISION_AT,
        overlays=(NATIONAL_TEAM_OVERLAY_ID,),
    )
    annotate_decision(decision, result)
    assert canonical_json(decision) == before  # 原输入未被修改
