"""ScanProfile 领域（P4.2）：版本化扫描方案。

- profile = 策略组合 + 各策略配置；版本化（PK: profile_id, version）。
- config_hash 覆盖全部策略配置（A/B 分支集合守恒验收用）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ab_screener.domain.data_point import canonical_json


def profile_config_hash(configs: dict[str, dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json(configs).encode("utf-8")).hexdigest()[:16]


def profile_id_for(name: str) -> str:
    """profile_id 基于 name（跨版本稳定；版本是 profile_id 下的分支）。"""
    return hashlib.sha256(canonical_json({"name": name}).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ScanProfile:
    name: str
    version: str
    strategy_ids: tuple[str, ...]
    configs: dict[str, dict[str, Any]]
    status: str = "DRAFT"          # DRAFT / ACTIVE / RETIRED
    profile_id: str = ""
    config_hash: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ValueError("ScanProfile 必须携带 name 与 version")
        if not self.strategy_ids:
            raise ValueError("ScanProfile 至少需要一个策略")
        if set(self.configs) != set(self.strategy_ids):
            raise ValueError("configs 键必须与 strategy_ids 一致")
        object.__setattr__(self, "profile_id", profile_id_for(self.name))
        object.__setattr__(self, "config_hash", profile_config_hash(self.configs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "version": self.version,
            "strategy_ids": list(self.strategy_ids),
            "configs": self.configs,
            "config_hash": self.config_hash,
            "status": self.status,
        }
