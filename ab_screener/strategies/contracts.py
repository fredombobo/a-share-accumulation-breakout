"""六形态策略插件契约（P4.1）。

- 选择定义与执行时点分离：每个插件有独立 `strategy_definition_id/hash`，
  共同引用 `NEXT_TRADABLE_OPEN_EXECUTION_V1`（统一执行核心的下一可交易日开盘）。
- 插件状态机：EXPERIMENTAL → REJECTED | CANDIDATE → SHADOW → ACTIVE_FOR_A_POOL → RETIRED。
  工程实现只要求完成 EXPERIMENTAL 契约；只有独立 R/S 门禁通过后才允许 ACTIVE_FOR_A_POOL。
- 防守 overlay 不计作第六形态：只改变开仓许可和 WATCHING 展示，不直接产生买单。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ab_screener.domain.data_point import canonical_json

NEXT_TRADABLE_OPEN_EXECUTION_V1 = "NEXT_TRADABLE_OPEN_EXECUTION_V1"

STRATEGY_STATES = (
    "EXPERIMENTAL", "REJECTED", "CANDIDATE", "SHADOW", "ACTIVE_FOR_A_POOL", "RETIRED",
)

# 工程实现只要求六插件完成 EXPERIMENTAL；ACTIVE_FOR_A_POOL 必须等独立 R/S 门禁
DEFAULT_RESEARCH_STATUS = "EXPERIMENTAL"


@dataclass(frozen=True)
class StrategySpec:
    """一个 selection 插件的不可变定义（注册后不可修改）。"""

    strategy_definition_id: str
    version: str
    economic_assumption: str      # 经济假设
    failure_conditions: str       # 失效条件
    pit_test: str                 # 防未来函数测试说明
    golden_fixture: str           # golden fixture 引用
    research_status: str = DEFAULT_RESEARCH_STATUS
    config_path: str = ""

    def __post_init__(self) -> None:
        if not self.strategy_definition_id or not self.version:
            raise ValueError("策略定义必须携带 id 与 version")
        for name, value in (
            ("economic_assumption", self.economic_assumption),
            ("failure_conditions", self.failure_conditions),
            ("pit_test", self.pit_test),
            ("golden_fixture", self.golden_fixture),
        ):
            if not value or not str(value).strip():
                raise ValueError(f"策略 {self.strategy_definition_id} 缺少 {name}")


@dataclass(frozen=True)
class SignalObservation:
    """不可变信号观察（原始发现；状态推进由 signal_lifecycle 负责）。"""

    strategy_definition_id: str
    strategy_hash: str
    input_hash: str
    snapshot_id: str
    ts_code: str
    signal_date: str
    config_hash: str
    payload: dict[str, Any]
    explanation: str
    tradeable: bool
    entry_definition_id: str = NEXT_TRADABLE_OPEN_EXECUTION_V1
    observation_id: str = ""

    def __post_init__(self) -> None:
        if not self.observation_id:
            object.__setattr__(
                self, "observation_id",
                observation_id_for(
                    strategy_definition_id=self.strategy_definition_id,
                    ts_code=self.ts_code,
                    signal_date=self.signal_date,
                    snapshot_id=self.snapshot_id,
                    input_hash=self.input_hash,
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "strategy_definition_id": self.strategy_definition_id,
            "strategy_hash": self.strategy_hash,
            "input_hash": self.input_hash,
            "snapshot_id": self.snapshot_id,
            "ts_code": self.ts_code,
            "signal_date": self.signal_date,
            "config_hash": self.config_hash,
            "payload": self.payload,
            "explanation": self.explanation,
            "tradeable": self.tradeable,
            "entry_definition_id": self.entry_definition_id,
        }


def observation_id_for(
    *,
    strategy_definition_id: str,
    ts_code: str,
    signal_date: str,
    snapshot_id: str,
    input_hash: str,
) -> str:
    """观察幂等键：同策略/标的/日期/快照/输入 → 同 id（重跑幂等）。"""
    blob = canonical_json(
        {
            "strategy": strategy_definition_id,
            "ts_code": ts_code,
            "signal_date": signal_date,
            "snapshot": snapshot_id,
            "input": input_hash,
        }
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def strategy_hash(spec: StrategySpec) -> str:
    blob = canonical_json(
        {
            "id": spec.strategy_definition_id,
            "version": spec.version,
            "assumption": spec.economic_assumption,
            "failure": spec.failure_conditions,
            "config_path": spec.config_path,
        }
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class OverlayDecision:
    """防守 overlay 输出：只改变开仓许可与展示，不产生买单。"""

    allow_new_entries: bool
    reason: str
    mode: str  # defensive / neutral / aggressive
