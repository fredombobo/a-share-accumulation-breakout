"""Shared, data-backed market classification definitions.

Only fields that exist in the local ``stock_basic`` snapshot are published.  A
classification definition describes a current grouping dimension; it does not
claim historical point-in-time membership.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

ClassificationKey = Literal["industry", "market", "area"]
DEFAULT_CLASSIFICATION: ClassificationKey = "industry"


@dataclass(frozen=True)
class ClassificationDefinition:
    key: ClassificationKey
    column: str
    title: str
    group_label: str
    description: str

    def public(self) -> dict[str, str]:
        return {
            "key": self.key,
            "title": self.title,
            "group_label": self.group_label,
            "description": self.description,
            "pit_status": "CURRENT_SNAPSHOT_ONLY",
        }


CLASSIFICATIONS: tuple[ClassificationDefinition, ...] = (
    ClassificationDefinition(
        key="industry",
        column="industry",
        title="细分行业",
        group_label="行业",
        description="按本地 stock_basic 行业字段分组，适合查看产业细分方向。",
    ),
    ClassificationDefinition(
        key="market",
        column="market",
        title="上市板块",
        group_label="板块",
        description="按主板、创业板、科创板等上市板块分组。",
    ),
    ClassificationDefinition(
        key="area",
        column="area",
        title="地域",
        group_label="地区",
        description="按公司注册地区分组，适合观察区域资金与区域股票池。",
    ),
)

_BY_KEY = {item.key: item for item in CLASSIFICATIONS}


def get_classification(value: str | None) -> ClassificationDefinition:
    key = str(value or DEFAULT_CLASSIFICATION).strip().lower()
    item = _BY_KEY.get(cast(ClassificationKey, key))
    if item is None:
        allowed = ", ".join(_BY_KEY)
        raise ValueError(f"不支持的分类标准 {key!r}，允许值：{allowed}")
    return item


def classification_catalog() -> list[dict[str, str]]:
    return [item.public() for item in CLASSIFICATIONS]


def normalize_group(value: object) -> str:
    text = str(value or "").strip()
    return text if text and text.lower() != "nan" else "未分类"
