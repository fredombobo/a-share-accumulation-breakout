"""ENTRY 定义注册表：唯一解析入口，保存 ID + semantic hash。

契约（implementation P0.2）：
- 所有消费者通过 registry 显式解析定义；报告声明 V1 但 hash 不匹配 → 拒绝生成。
- 未知版本 fail-closed；默认生产候选 ACTIVE=A_POOL_STRICT_NEXT_OPEN_V1。
- 激活 V2 不改变 V1 结果（V1 golden 测试锁定）。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from ab_screener.domain import entry_definition, entry_definition_v2

REGISTRY: dict[str, dict[str, Any]] = {}


def _register() -> None:
    if REGISTRY:
        return
    REGISTRY[entry_definition.ENTRY_DEFINITION_ID] = {
        "snapshot": entry_definition.definition_snapshot(),
    }
    REGISTRY[entry_definition_v2.ENTRY_DEFINITION_ID] = entry_definition_v2.registry_entry()


_register()


def registered_definition_ids() -> list[str]:
    return sorted(REGISTRY)


def semantic_hash(definition_id: str) -> str:
    """定义语义哈希：快照 JSON 的 SHA-256（报告指纹用）。"""
    _ensure_registered(definition_id)
    blob = json.dumps(REGISTRY[definition_id]["snapshot"], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def resolve_definition(definition_id: str) -> dict[str, Any]:
    """解析定义快照；未知版本抛错（fail-closed）。"""
    _ensure_registered(definition_id)
    return dict(REGISTRY[definition_id]["snapshot"])


def _ensure_registered(definition_id: str) -> None:
    if definition_id not in REGISTRY:
        raise ValueError(
            f"未知 ENTRY 定义: {definition_id}（已注册: {registered_definition_ids()}）"
        )


def active_definition_id() -> str:
    """生产候选定义：默认 V1；经独立研究通过后才允许显式切 V2。"""
    raw = os.environ.get("ACTIVE_ENTRY_DEFINITION_ID", "").strip()
    if raw:
        _ensure_registered(raw)
        return raw
    return entry_definition.ENTRY_DEFINITION_ID


def report_entry_fingerprint(definition_id: str) -> dict[str, str]:
    """报告引用的定义指纹：id + semantic hash。hash 不匹配即拒绝由调用方判定。"""
    return {"entry_definition_id": definition_id, "entry_semantic_hash": semantic_hash(definition_id)}


def verify_report_entry_fingerprint(report: dict[str, Any]) -> None:
    """校验报告声明的入场定义指纹；声明与注册表当前语义不符 → 抛错（fail-closed）。

    用途：复用/追加旧报告前校验「声明 V1 但 hash 已漂移」的静默误用。
    报告未声明 ID 或未声明 hash 时同样拒绝（缺证据 = 不信任）。
    """
    declared_id = report.get("entry_definition_id")
    if not declared_id:
        raise ValueError("报告缺少 entry_definition_id，无法校验入场定义指纹")
    declared_hash = report.get("entry_semantic_hash")
    if not declared_hash:
        raise ValueError(f"报告缺少 entry_semantic_hash（{declared_id}），拒绝复用/生成")
    expected = semantic_hash(declared_id)
    if declared_hash != expected:
        raise ValueError(
            f"报告声明 {declared_id} 但语义哈希 {declared_hash} ≠ 注册表当前 {expected}；"
            "入场定义已漂移，拒绝按旧语义继续生成/复用"
        )
