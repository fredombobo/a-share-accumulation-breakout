"""NTM（national-team-monitor）快照只读适配器（V2R-N）。

把供应商固定离线原始响应解析为带 PIT 语义的领域记录，供信息覆盖层解释/研究观察使用。

契约（V2R-N / 2026-08-22-ntm-p1-overlay.md §3 A3，只读边界优先）：
- 领域记录携带 `observation_at/effective_at/available_at/ingested_at/source/revision/
  confidence/evidence_refs` 八元组；所有时间为带时区（+08:00）ISO 时间戳。
- 缺 source / 权限不足 / 字段缺失 / 未知 verdict / 外部时间无时区 → 结构化
  `OverlayInsufficient`，绝不抛无结构异常、绝不伪造供应商字段。
- 供应商不可用（None / 文件缺失 / 损坏 JSON）→ `vendor_unavailable`。
- 本模块只做解析与归一化；PIT 求值（available_at vs decision_at）在领域层
  `ab_screener/intelligence/national_team_overlay_v1.py`。

隔离约束：只允许标准库 json/os/pathlib/datetime/typing/dataclasses 与领域 data_point；
不触网、不读库、不写任何账本。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from ab_screener.domain.data_point import TZ_SH, canonical_json, normalize_ts

SOURCE_NTM = "ntm"
SCHEMA_VERSION = 1
KNOWN_VERDICTS = ("危险共振", "机会共振", "中性")
CONFIDENCE_LEVELS = ("low", "medium", "high")
PERMISSION_SCOPE_REQUIRED = "resonance"


def _now() -> str:
    """本地真实读取/入库时点；不得复用供应商 generated_at 伪造。"""
    return normalize_ts(datetime.now(TZ_SH))


def parse_ts_aware(value: Any) -> datetime:
    """解析时间戳：必须是带时区的 ISO 时间，无时区/不可解析 → ValueError。

    供适配器与领域层共用：覆盖层所有时间为带时区时间戳，无时区一律拒绝
    （不默认补时区，避免把外部未知时区误当成 Asia/Shanghai）。
    """
    if value is None or value == "":
        raise ValueError("时间字段缺失")
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError(f"时间必须带时区: {value!r}")
    return parsed


@dataclass(frozen=True)
class NationalTeamObservation:
    """国家队资金领域观测（PIT-safe 只读）。

    - observation_at：观测发生时刻（= 供应商 generated_at）。
    - effective_at：观测对应的市场日期（= 快照 as_of，按 +08:00 零点）。
    - available_at：该信息真实可用时刻（PIT 门禁：仅 available_at <= decision_at 可读）。
    - ingested_at：本地入库时刻（默认 = available_at）。
    - source / revision / confidence / evidence_refs：来源、修订、置信度、证据引用。
    """

    observation_at: str
    effective_at: str
    available_at: str
    ingested_at: str
    source: str
    revision: str
    confidence: str
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    verdict: str = ""
    red_count: int = 0
    green_count: int = 0
    total: int = 0
    per_etf: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("observation_at", "effective_at", "available_at", "ingested_at"):
            raw = getattr(self, name)
            normalized = normalize_ts(parse_ts_aware(raw))
            object.__setattr__(self, name, normalized)
        if not self.source or not str(self.source).strip():
            raise ValueError("观测记录缺少 source")
        if not self.revision or not str(self.revision).strip():
            raise ValueError("观测记录缺少 revision")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"未知 confidence: {self.confidence!r}")
        if self.verdict not in KNOWN_VERDICTS:
            raise ValueError(f"未知 verdict: {self.verdict!r}")
        for name in ("red_count", "green_count", "total"):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} 不能为负")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_at": self.observation_at,
            "effective_at": self.effective_at,
            "available_at": self.available_at,
            "ingested_at": self.ingested_at,
            "source": self.source,
            "revision": self.revision,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "verdict": self.verdict,
            "red_count": self.red_count,
            "green_count": self.green_count,
            "total": self.total,
            "per_etf": [dict(item) for item in self.per_etf],
        }


@dataclass(frozen=True)
class OverlayInsufficient:
    """结构化 INSUFFICIENT：覆盖层求值/解析失败时不抛异常，返回此结果。"""

    status: str = "INSUFFICIENT"
    reason: str = ""
    detail: str = ""
    decision_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "decision_at": self.decision_at,
        }


def _insufficient(reason: str, detail: str) -> OverlayInsufficient:
    return OverlayInsufficient(reason=reason, detail=detail)


def _compact_ts(normalized: str) -> str:
    """2026-08-21T18:30:00+08:00 → 20260821T183000+0800（确定性 revision 组成）。"""
    return normalized.replace("-", "").replace(":", "")


def parse_ntm_snapshot(
    raw: dict[str, Any] | None,
    *,
    ingested_at: str | None = None,
) -> NationalTeamObservation | OverlayInsufficient:
    """固定离线原始响应 → 领域观测记录，或结构化 INSUFFICIENT。"""
    if raw is None or not isinstance(raw, dict):
        return _insufficient("vendor_unavailable", "NTM 快照未配置或不可用")
    # 1) source：必须为已知供应商，否则拒绝（不伪造供应商能力）
    source = raw.get("source")
    if source != SOURCE_NTM:
        return _insufficient("missing_source", f"缺失/未知 source（需要 {SOURCE_NTM!r}）")
    # 2) permission：权限不足或权限范围不明确 → 拒绝
    permission = raw.get("permission")
    if not isinstance(permission, dict):
        return _insufficient("insufficient_permission", "permission 缺失或格式不明确")
    if permission.get("granted") is not True:
        return _insufficient("insufficient_permission", "供应商权限未授予")
    scope = permission.get("scope")
    if not isinstance(scope, list) or PERMISSION_SCOPE_REQUIRED not in scope:
        return _insufficient(
            "insufficient_permission",
            f"权限范围缺少 {PERMISSION_SCOPE_REQUIRED}: {scope!r}",
        )
    # 3) 必填字段
    missing: list[str] = []
    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        missing.append("schema_version")
    as_of = raw.get("as_of")
    if not isinstance(as_of, str):
        missing.append("as_of")
    else:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            missing.append("as_of")
    generated_at = raw.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        missing.append("generated_at")
    confidence = raw.get("confidence")
    if confidence not in CONFIDENCE_LEVELS:
        missing.append("confidence")
    resonance = raw.get("resonance")
    if not isinstance(resonance, dict):
        missing.append("resonance")
    else:
        if not isinstance(resonance.get("verdict"), str):
            missing.append("verdict")
        for count_field in ("red_count", "green_count", "total"):
            if not isinstance(resonance.get(count_field), int):
                missing.append(count_field)
    evidence_refs = raw.get("evidence_refs")
    if not isinstance(evidence_refs, list) or any(
        not isinstance(item, str) for item in evidence_refs
    ):
        missing.append("evidence_refs")
    if missing:
        return _insufficient("missing_fields", f"缺失/非法字段: {sorted(missing)}")
    assert isinstance(resonance, dict), "resonance 缺失时已在上面返回 INSUFFICIENT"
    evidence_ref_values = cast(list[str], evidence_refs)
    # 4) 外部时间必须带时区
    try:
        observation_at = normalize_ts(parse_ts_aware(generated_at))
    except ValueError as exc:
        return _insufficient("invalid_timestamp", f"generated_at 无效: {exc}")
    try:
        effective_at = normalize_ts(
            datetime.combine(date.fromisoformat(str(as_of)), datetime.min.time()).replace(
                tzinfo=TZ_SH
            )
        )
    except ValueError as exc:
        return _insufficient("invalid_timestamp", f"as_of 无效: {exc}")
    available_at = observation_at
    # 5) ingested_at：必须带时区；缺省取本地真实读取时点，不能伪装成 available_at
    if ingested_at is None:
        ingested_at_value = _now()
    else:
        try:
            ingested_at_value = normalize_ts(parse_ts_aware(ingested_at))
        except ValueError as exc:
            return _insufficient("invalid_timestamp", f"ingested_at 无效: {exc}")
    # 6) 置信度：degraded/warnings 非空 → 不得高估，封顶 low
    degraded = raw.get("degraded") or []
    warnings = raw.get("warnings") or []
    confidence_value = "low" if (degraded or warnings) else str(confidence)
    # 7) verdict：未知 → 不解释（不伪造）
    verdict = str(resonance["verdict"])
    if verdict not in KNOWN_VERDICTS:
        return _insufficient("unknown_verdict", f"未知 verdict: {verdict!r}")
    per_etf = resonance.get("per_etf") or []
    per_etf_rows = tuple(
        dict(item) for item in per_etf if isinstance(item, dict)
    )
    return NationalTeamObservation(
        observation_at=observation_at,
        effective_at=effective_at,
        available_at=available_at,
        ingested_at=ingested_at_value,
        source=str(source),
        revision=f"schema_v{SCHEMA_VERSION}_{_compact_ts(observation_at)}",
        confidence=confidence_value,
        evidence_refs=tuple(evidence_ref_values),
        verdict=verdict,
        red_count=int(resonance["red_count"]),
        green_count=int(resonance["green_count"]),
        total=int(resonance["total"]),
        per_etf=per_etf_rows,
    )


def read_ntm_snapshot(
    path: str | Path | None,
) -> NationalTeamObservation | OverlayInsufficient:
    """只读本地快照 JSON 文件（不触网）。缺失/损坏 → vendor_unavailable。"""
    if not path:
        return _insufficient("vendor_unavailable", "NTM_SNAPSHOT_PATH 未配置")
    p = Path(path).expanduser()
    if not p.is_file():
        return _insufficient("vendor_unavailable", f"快照文件缺失: {p}")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return _insufficient("vendor_unavailable", f"快照文件不可读/损坏: {p}")
    return parse_ntm_snapshot(payload if isinstance(payload, dict) else None)


def observation_fingerprint(record: NationalTeamObservation) -> str:
    """观测记录规范化指纹（校验/审计用）。"""
    return canonical_json(record.to_dict())
