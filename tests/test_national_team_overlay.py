"""国家队/机构资金信息覆盖层领域测试（V2R-N）：PIT-safe 只读。

契约：
- 领域记录携带 observation_at/effective_at/available_at/ingested_at/source/
  revision/confidence/evidence_refs；所有时间为带时区时间戳。
- 历史决策只读 available_at <= decision_at 的记录；
  available_at > decision_at → 结构化 INSUFFICIENT（看不到未来记录）。
- 无 source / 权限不足 / 字段缺失 / 供应商不可用 → 结构化 INSUFFICIENT。
- 覆盖层只解释、标记或研究排序；不得注册进开仓许可 registry。
"""
from __future__ import annotations

import copy
from datetime import datetime

from ab_screener.data.adapters.ntm_client import (
    SOURCE_NTM,
    NationalTeamObservation,
    OverlayInsufficient,
    parse_ntm_snapshot,
)
from ab_screener.intelligence.national_team_overlay_v1 import (
    NATIONAL_TEAM_OVERLAY_ID,
    SPEC,
    evaluate,
    observation_hash,
    records_for_decision,
)

# 冻结时点（+08:00，带时区）
DECISION_AT_EVENING = "2026-08-21T20:00:00+08:00"
DECISION_AT_MORNING = "2026-08-21T10:00:00+08:00"  # 早于快照 generated_at（未来信息）

FROZEN_RAW = {
    "source": SOURCE_NTM,
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


def _obs() -> NationalTeamObservation:
    result = parse_ntm_snapshot(copy.deepcopy(FROZEN_RAW))
    assert isinstance(result, NationalTeamObservation)
    return result


def _raw_at(generated_at: str) -> NationalTeamObservation:
    """构造指定 generated_at（available_at）的观测记录（深拷贝避免污染）。"""
    raw = copy.deepcopy(FROZEN_RAW)
    raw["generated_at"] = generated_at
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, NationalTeamObservation)
    return result


def test_observation_record_timestamps_are_timezone_aware():
    """领域记录 8 个契约字段齐全，时间为带时区 ISO 时间戳。"""
    obs = _obs()
    for ts_field in (
        "observation_at",
        "effective_at",
        "available_at",
        "ingested_at",
    ):
        value = getattr(obs, ts_field)
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, ts_field
        assert value.endswith("+08:00"), ts_field
    assert obs.source == SOURCE_NTM
    assert obs.revision
    assert obs.confidence in ("low", "medium", "high")
    assert isinstance(obs.evidence_refs, tuple)
    # to_dict 输出契约字段
    d = obs.to_dict()
    for key in (
        "observation_at", "effective_at", "available_at", "ingested_at",
        "source", "revision", "confidence", "evidence_refs",
    ):
        assert key in d, key


def test_evaluate_available_before_decision_is_readable():
    obs = evaluate(_obs(), decision_at=DECISION_AT_EVENING)
    assert isinstance(obs, NationalTeamObservation)
    assert obs.verdict == "机会共振"


def test_evaluate_future_information_is_insufficient():
    """available_at > decision_at → INSUFFICIENT（历史决策看不到未来记录）。"""
    obs = _obs()
    assert obs.available_at > DECISION_AT_MORNING
    result = evaluate(obs, decision_at=DECISION_AT_MORNING)
    assert isinstance(result, OverlayInsufficient)
    assert result.status == "INSUFFICIENT"
    assert result.reason == "future_information"
    assert result.decision_at == DECISION_AT_MORNING


def test_evaluate_none_is_vendor_unavailable():
    result = evaluate(None, decision_at=DECISION_AT_EVENING)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "vendor_unavailable"


def test_evaluate_invalid_decision_at_is_insufficient():
    for bad in ("2026-08-21T20:00:00", "not-a-date", ""):
        result = evaluate(_obs(), decision_at=bad)
        assert isinstance(result, OverlayInsufficient), bad
        assert result.reason == "invalid_decision_at", bad


def test_records_for_decision_filters_future_records():
    """历史读取只返回 available_at <= decision_at 的记录。"""
    early = _raw_at("2026-08-21T09:00:00+08:00")  # available 09:00 <= 决策 10:00
    future = _obs()  # available_at = 2026-08-21T18:30:00+08:00（未来）
    visible = records_for_decision([future, early], DECISION_AT_MORNING)
    assert [r.available_at for r in visible] == [early.available_at]
    assert future not in visible


def test_records_for_decision_all_future_is_empty():
    """全部记录 available_at > decision_at → 空（看不到任何未来记录）。"""
    visible = records_for_decision([_obs()], DECISION_AT_MORNING)
    assert visible == ()


def test_records_for_decision_keeps_earlier_sorted():
    obs = _obs()
    visible = records_for_decision([obs], DECISION_AT_EVENING)
    assert visible == (obs,)


def test_records_for_decision_invalid_decision_at_fail_closed():
    """决策时点无效（无时区/不可解析）→ 读不到任何记录（fail-closed）。"""
    assert records_for_decision([_obs()], "2026-08-21T20:00:00") == ()
    assert records_for_decision([_obs()], "garbage") == ()


def test_observation_hash_is_deterministic():
    a = observation_hash(_obs())
    b = observation_hash(_obs())
    assert a == b
    assert len(a) == 16
    assert isinstance(a, str)


def test_observation_hash_changes_with_content():
    raw = copy.deepcopy(FROZEN_RAW)
    raw["resonance"] = dict(raw["resonance"], verdict="危险共振")
    other = parse_ntm_snapshot(raw)
    assert isinstance(other, NationalTeamObservation)
    assert observation_hash(_obs()) != observation_hash(other)


def test_overlay_exposes_id_and_spec():
    assert NATIONAL_TEAM_OVERLAY_ID == "national_team_overlay_v1"
    assert SPEC.strategy_definition_id == NATIONAL_TEAM_OVERLAY_ID
    assert SPEC.version == "v1"
    assert SPEC.research_status == "EXPERIMENTAL"
    assert "configs/intelligence/national_team_overlay_v1.yaml" == SPEC.config_path


def test_overlay_not_registered_into_open_permission_registry():
    """覆盖层不进入 regime overlay 开仓许可 registry（只读边界）。"""
    from ab_screener.regimes.registry import regime_overlays

    assert NATIONAL_TEAM_OVERLAY_ID not in regime_overlays()


def test_overlay_result_never_mentions_position_or_orders():
    """观测记录不含仓位/订单/资格字段（只读注释）。"""
    obs = _obs()
    d = obs.to_dict()
    for forbidden in ("allow_new_entries", "target_position", "orders",
                      "a_pool_eligible", "b_pool_eligible"):
        assert forbidden not in d, forbidden
