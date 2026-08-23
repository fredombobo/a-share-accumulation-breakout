"""NTM 快照只读适配器测试（V2R-N）：完全离线、固定原始响应、无网络。

契约：
- `parse_ntm_snapshot` 用固定离线原始响应解析出
  observation_at/effective_at/available_at/ingested_at/source/revision/confidence/evidence_refs。
- 缺 source / 权限不足 / 字段缺失 / 未知 verdict / 时间无时区 → 结构化 INSUFFICIENT，
  不抛无结构异常、不伪造供应商字段。
- 供应商不可用（None / 文件缺失 / 损坏 JSON）→ INSUFFICIENT，不伪造记录。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from ab_screener.data.adapters.ntm_client import (
    CONFIDENCE_LEVELS,
    KNOWN_VERDICTS,
    SOURCE_NTM,
    NationalTeamObservation,
    OverlayInsufficient,
    parse_ntm_snapshot,
    read_ntm_snapshot,
)

# 固定离线原始响应（冻结时点，不带任何实时数据）
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
        "per_etf": [
            {"code": "510300", "verdict": "中性", "red": 1, "green": 4, "indicators": {}},
        ],
    },
    "evidence_refs": ["E:/CODEX/national-team-monitor/runtime/snapshot_20260821.json"],
    "degraded": [],
    "warnings": [],
}


def _raw(**overrides: object) -> dict:
    """FROZEN_RAW 深拷贝 + 覆盖（避免嵌套 dict 跨用例共享/污染）。"""
    return {**copy.deepcopy(FROZEN_RAW), **overrides}


def test_parse_frozen_raw_extracts_all_contract_fields():
    """固定原始响应 → 解析出领域记录的 8 个契约字段。"""
    obs = parse_ntm_snapshot(FROZEN_RAW)
    assert isinstance(obs, NationalTeamObservation)
    assert obs.observation_at == "2026-08-21T18:30:00+08:00"
    assert obs.effective_at == "2026-08-21T00:00:00+08:00"
    assert obs.available_at == "2026-08-21T18:30:00+08:00"
    assert obs.ingested_at == "2026-08-21T18:30:00+08:00"
    assert obs.source == SOURCE_NTM
    assert obs.revision == "schema_v1_20260821T183000+0800"
    assert obs.confidence in CONFIDENCE_LEVELS
    assert obs.confidence == "medium"
    assert obs.evidence_refs == (
        "E:/CODEX/national-team-monitor/runtime/snapshot_20260821.json",
    )
    # 载荷
    assert obs.verdict == "机会共振"
    assert obs.red_count == 1
    assert obs.green_count == 4
    assert obs.total == 5
    assert len(obs.per_etf) == 1


def test_parse_injects_ingested_at():
    """调用方可注入 ingested_at（本地入库时点）。"""
    obs = parse_ntm_snapshot(FROZEN_RAW, ingested_at="2026-08-21T19:05:00+08:00")
    assert isinstance(obs, NationalTeamObservation)
    assert obs.ingested_at == "2026-08-21T19:05:00+08:00"


def test_parse_none_is_vendor_unavailable():
    result = parse_ntm_snapshot(None)
    assert isinstance(result, OverlayInsufficient)
    assert result.status == "INSUFFICIENT"
    assert result.reason == "vendor_unavailable"


def test_parse_non_dict_is_vendor_unavailable():
    result = parse_ntm_snapshot([1, 2, 3])  # type: ignore[arg-type]
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "vendor_unavailable"


def test_parse_missing_source_is_insufficient():
    raw = _raw(); del raw["source"]
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "missing_source"
    assert result.status == "INSUFFICIENT"


def test_parse_unknown_source_is_insufficient():
    raw = _raw(source="unknown_vendor")
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "missing_source"


def test_parse_permission_denied_is_insufficient():
    raw = _raw(permission={"granted": False, "scope": ["resonance"]})
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "insufficient_permission"


def test_parse_permission_scope_missing_resonance_is_insufficient():
    raw = _raw(permission={"granted": True, "scope": ["holders"]})
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "insufficient_permission"


def test_parse_missing_required_fields_is_insufficient():
    for missing in ("as_of", "generated_at", "confidence", "schema_version"):
        raw = _raw()
        del raw[missing]
        result = parse_ntm_snapshot(raw)
        assert isinstance(result, OverlayInsufficient), missing
        assert result.reason == "missing_fields", missing
        assert missing in result.detail, missing


def test_parse_missing_resonance_verdict_is_insufficient():
    raw = _raw()
    del raw["resonance"]["verdict"]
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "missing_fields"
    assert "verdict" in result.detail


def test_parse_unknown_verdict_is_insufficient():
    raw = _raw()
    raw["resonance"] = dict(raw["resonance"], verdict="乱七八糟")
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "unknown_verdict"


def test_parse_generated_at_naive_is_rejected():
    """外部快照时间必须带时区；无时区 → INSUFFICIENT，不默认补时区。"""
    raw = _raw(generated_at="2026-08-21T18:30:00")
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "invalid_timestamp"


def test_parse_generated_at_unparseable_is_rejected():
    raw = _raw(generated_at="not-a-timestamp")
    result = parse_ntm_snapshot(raw)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "invalid_timestamp"


def test_parse_ingested_at_naive_is_rejected():
    result = parse_ntm_snapshot(FROZEN_RAW, ingested_at="2026-08-21T19:05:00")
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "invalid_timestamp"


def test_parse_evidence_refs_default_empty():
    raw = _raw(); del raw["evidence_refs"]
    obs = parse_ntm_snapshot(raw)
    assert isinstance(obs, NationalTeamObservation)
    assert obs.evidence_refs == ()


def test_parse_degraded_caps_confidence_to_low():
    raw = _raw(degraded=["510300"])
    obs = parse_ntm_snapshot(raw)
    assert isinstance(obs, NationalTeamObservation)
    assert obs.confidence == "low"


def test_parse_verdicts_are_known():
    assert "危险共振" in KNOWN_VERDICTS
    assert "机会共振" in KNOWN_VERDICTS
    assert "中性" in KNOWN_VERDICTS


def test_read_ntm_snapshot_missing_file_does_not_raise(tmp_path: Path):
    missing = tmp_path / "no_such_snapshot.json"
    result = read_ntm_snapshot(missing)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "vendor_unavailable"


def test_read_ntm_snapshot_malformed_file_does_not_raise(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    result = read_ntm_snapshot(bad)
    assert isinstance(result, OverlayInsufficient)
    assert result.reason == "vendor_unavailable"


def test_read_ntm_snapshot_valid_file_parses(tmp_path: Path):
    good = tmp_path / "snapshot.json"
    good.write_text(json.dumps(FROZEN_RAW, ensure_ascii=False), encoding="utf-8")
    result = read_ntm_snapshot(good)
    assert isinstance(result, NationalTeamObservation)
    assert result.verdict == "机会共振"


def test_insufficient_is_structured_not_exception():
    result = parse_ntm_snapshot(None)
    assert isinstance(result, OverlayInsufficient)
    d = result.to_dict()
    assert d["status"] == "INSUFFICIENT"
    assert d["reason"] == "vendor_unavailable"
    assert isinstance(d["detail"], str)
