"""席位名称标准化与身份假设（T05）。映射按事件日有效版本读取，禁止用今天回填历史。"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ab_screener.domain.lhb_contracts import (
    ACTOR_TYPE_VALUES,
    EVIDENCE_GRADE_VALUES,
    HOT_MONEY_MAX_EVIDENCE_GRADE,
    OFFICIAL_TAG_VALUES,
    identity_display,
    parse_enum,
    parse_trade_date,
)

LEGAL_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司")


def nfkc_name(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw or "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", "", text)
    return text.strip()


def classify_official_tag(raw: str) -> str:
    name = nfkc_name(raw)
    if "沪股通专用" in name or name.startswith("沪股通"):
        return "SH_CONNECT"
    if "深股通专用" in name or name.startswith("深股通"):
        return "SZ_CONNECT"
    if "机构专用" in name:
        return "INSTITUTION_CHANNEL"
    if "非营业场所" in name or name.endswith("总部") or "总部" in name:
        return "HQ_NON_BRANCH"
    if "营业部" in name or "分公司" in name:
        return "BRANCH"
    return "UNKNOWN"


def actor_type_for_tag(tag: str) -> str:
    parse_enum(tag, OFFICIAL_TAG_VALUES, label="official_tag")
    if tag == "INSTITUTION_CHANNEL":
        return "INSTITUTION_CHANNEL"
    if tag in {"SH_CONNECT", "SZ_CONNECT"}:
        return "CONNECT_CHANNEL"
    return "UNKNOWN"


def seat_id_for(canonical_name: str) -> str:
    return hashlib.sha256(canonical_name.encode("utf-8")).hexdigest()[:16]


def hot_money_actor_id(hm_name: str) -> str:
    """人物候选 ID 只由人物名决定，确保一人多席位可聚合。"""
    normalized = nfkc_name(hm_name)
    if not normalized:
        raise ValueError("hm_name must not be empty")
    return "hm:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def canonical_seat_name(raw: str) -> str:
    name = nfkc_name(raw)
    if not name:
        raise ValueError("seat name must not be empty")
    if name in {"机构专用", "机构专用席位"}:
        return "机构专用"
    for suffix in LEGAL_SUFFIXES:
        name = name.replace(suffix, "")
    return re.sub(r"(?:证券)?营业部$", "", name)


@dataclass(frozen=True)
class SeatHypothesis:
    seat_id: str
    actor_id: str
    seat_raw: str
    canonical_name: str
    official_tag: str
    actor_type: str
    display_name: str
    evidence_grade: str
    evidence_source: str
    valid_from: str
    valid_to: str | None
    conflict: bool = False

    def as_of_ok(self, event_date: str) -> bool:
        day = parse_trade_date(event_date)
        if day < self.valid_from:
            return False
        return not (self.valid_to and day >= self.valid_to)


def hypothesis_from_raw(
    raw: str,
    *,
    event_date: str,
    hm_name: str | None = None,
    evidence_source: str = "official_tag",
    evidence_grade: str = "A",
    valid_from: str = "19900101",
    valid_to: str | None = None,
) -> SeatHypothesis:
    parse_trade_date(event_date)
    start = parse_trade_date(valid_from)
    end = parse_trade_date(valid_to) if valid_to else None
    if end and end <= start:
        raise ValueError("valid_to must be greater than valid_from")
    canonical = canonical_seat_name(raw)
    tag = classify_official_tag(raw)
    actor = actor_type_for_tag(tag)
    grade = parse_enum(evidence_grade, EVIDENCE_GRADE_VALUES, label="evidence_grade")
    if hm_name:
        actor = "HOT_MONEY_CANDIDATE"
        if grade == "A":
            grade = HOT_MONEY_MAX_EVIDENCE_GRADE
        if evidence_source == "official_tag":
            evidence_source = "hm_list"
    label = hm_name or canonical
    display = identity_display(actor_type=actor, label=label, evidence_grade=grade)
    return SeatHypothesis(
        seat_id=seat_id_for(canonical),
        actor_id=hot_money_actor_id(hm_name) if hm_name else seat_id_for(canonical),
        seat_raw=raw,
        canonical_name=canonical,
        official_tag=tag,
        actor_type=parse_enum(actor, ACTOR_TYPE_VALUES, label="actor_type"),
        display_name=display,
        evidence_grade=grade,
        evidence_source=evidence_source,
        valid_from=start,
        valid_to=end,
    )


def hypotheses_from_hm_list(
    rows: Iterable[dict[str, Any]],
    *,
    list_date: str,
    evidence_source: str = "tushare_hm_list",
) -> list[SeatHypothesis]:
    """把 hm_list 的人物 -> 多席位数组展开成多对多身份假设。"""
    day = parse_trade_date(list_date)
    out: list[SeatHypothesis] = []
    for row in rows:
        hm_name = nfkc_name(str(row.get("hm_name") or row.get("name") or ""))
        if not hm_name:
            raise ValueError("hm_list row missing name")
        raw_orgs = row.get("orgs")
        if isinstance(raw_orgs, str):
            try:
                orgs = json.loads(raw_orgs)
            except json.JSONDecodeError as exc:
                raise ValueError("hm_list orgs must be a JSON array") from exc
        else:
            orgs = raw_orgs
        if not isinstance(orgs, list) or any(not isinstance(org, str) for org in orgs):
            raise ValueError("hm_list orgs must be a list of strings")
        for org in orgs:
            if not nfkc_name(org):
                continue
            out.append(
                hypothesis_from_raw(
                    org,
                    event_date=day,
                    hm_name=hm_name,
                    evidence_source=evidence_source,
                    evidence_grade="B",
                    valid_from=day,
                )
            )
    return out


def detect_name_conflict(left: SeatHypothesis, right: SeatHypothesis) -> bool:
    """两个标准名称不同却被当成同一 seat_id，或同一原始名指向不同 seat_id。"""
    if left.seat_raw == right.seat_raw and left.seat_id != right.seat_id:
        return True
    return left.canonical_name != right.canonical_name and left.seat_id == right.seat_id


def precision_report(*, true_pairs: int, predicted_pairs: int, false_merges: int) -> dict[str, float]:
    coverage = (predicted_pairs / true_pairs) if true_pairs else 0.0
    mis_merge = (false_merges / predicted_pairs) if predicted_pairs else 0.0
    return {"coverage": coverage, "mis_merge_rate": mis_merge}


def precision_from_labeled_rows(
    rows: Iterable[dict[str, str]],
    *,
    min_coverage: float = 0.8,
    max_mis_merge_rate: float = 0.05,
) -> dict[str, float | int | bool]:
    """在人工标注别名样本上计算覆盖率和错误合并率。"""
    materialized = list(rows)
    predicted: list[tuple[str, str]] = []
    covered = 0
    for row in materialized:
        expected = nfkc_name(row.get("canonical") or "")
        actual = canonical_seat_name(row.get("alias_raw") or "")
        predicted.append((actual, expected))
        covered += int(actual == expected)
    grouped: dict[str, set[str]] = {}
    for actual, expected in predicted:
        grouped.setdefault(actual, set()).add(expected)
    false_merge_groups = sum(len(expected) > 1 for expected in grouped.values())
    sample_size = len(materialized)
    coverage = covered / sample_size if sample_size else 0.0
    mis_merge_rate = false_merge_groups / len(grouped) if grouped else 0.0
    return {
        "sample_size": sample_size,
        "coverage": coverage,
        "false_merge_groups": false_merge_groups,
        "mis_merge_rate": mis_merge_rate,
        "pass": coverage >= min_coverage and mis_merge_rate <= max_mis_merge_rate,
    }
