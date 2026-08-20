"""ENTRY v2 契约测试：定义快照结构、注册表解析、指纹校验 fail-closed。"""
from __future__ import annotations

import pytest

from ab_screener.domain.entry_definition import ENTRY_DEFINITION_ID as V1_ID
from ab_screener.domain.entry_definition_v2 import (
    ENTRY_DEFINITION_ID as V2_ID,
)
from ab_screener.domain.entry_definition_v2 import (
    V2_SEMANTIC_DELTAS,
    definition_snapshot,
)
from ab_screener.domain.entry_registry import (
    active_definition_id,
    registered_definition_ids,
    report_entry_fingerprint,
    resolve_definition,
    semantic_hash,
    verify_report_entry_fingerprint,
)


def test_v2_snapshot_structure():
    snap = definition_snapshot()
    assert snap["id"] == V2_ID
    assert snap["version"] == "v2"
    assert snap["base_on"] == V1_ID
    assert snap["semantic_deltas"] == V2_SEMANTIC_DELTAS


def test_v2_semantics_are_stricter_than_v1():
    v1 = resolve_definition(V1_ID)
    v2 = resolve_definition(V2_ID)
    # v2 必须显式声明与 V1 的差异，而不是悄悄改 V1
    assert v2["semantic_deltas"]["require_ma60"] is True
    assert v2["semantic_deltas"]["box_search"] == "two_step_breakout_first"
    assert v2["semantic_deltas"]["position_guard"] == "full_window_based_fail_closed"
    # v1 快照里不应出现 v2 专属字段
    assert "semantic_deltas" not in v1


def test_v2_hashes_differ_from_v1_and_are_stable():
    h1 = semantic_hash(V1_ID)
    h2 = semantic_hash(V2_ID)
    assert h1 != h2
    assert semantic_hash(V2_ID) == h2  # 确定性


def test_registry_registers_both_definitions():
    assert V1_ID in registered_definition_ids()
    assert V2_ID in registered_definition_ids()
    entry = resolve_definition(V2_ID)
    assert entry["id"] == V2_ID
    with pytest.raises(ValueError, match="未知 ENTRY 定义"):
        resolve_definition("A_POOL_STRICT_NEXT_OPEN_V9")


def test_report_fingerprint_shape(monkeypatch):
    monkeypatch.delenv("ACTIVE_ENTRY_DEFINITION_ID", raising=False)
    fp = report_entry_fingerprint(V1_ID)
    assert fp["entry_definition_id"] == V1_ID
    assert fp["entry_semantic_hash"] == semantic_hash(V1_ID)


def test_verify_fingerprint_ok(monkeypatch):
    monkeypatch.delenv("ACTIVE_ENTRY_DEFINITION_ID", raising=False)
    fp = report_entry_fingerprint(V1_ID)
    verify_report_entry_fingerprint({"entry_definition_id": V1_ID, "entry_semantic_hash": fp["entry_semantic_hash"]})


def test_verify_fingerprint_missing_id_raises():
    with pytest.raises(ValueError, match="缺少 entry_definition_id"):
        verify_report_entry_fingerprint({})


def test_verify_fingerprint_missing_hash_raises():
    with pytest.raises(ValueError, match="缺少 entry_semantic_hash"):
        verify_report_entry_fingerprint({"entry_definition_id": V1_ID})


def test_verify_fingerprint_mismatch_raises():
    with pytest.raises(ValueError, match="语义哈希"):
        verify_report_entry_fingerprint(
            {"entry_definition_id": V1_ID, "entry_semantic_hash": "0" * 16}
        )


def test_active_default_is_v1_and_env_switch(monkeypatch):
    monkeypatch.delenv("ACTIVE_ENTRY_DEFINITION_ID", raising=False)
    assert active_definition_id() == V1_ID
    monkeypatch.setenv("ACTIVE_ENTRY_DEFINITION_ID", V2_ID)
    assert active_definition_id() == V2_ID
    monkeypatch.setenv("ACTIVE_ENTRY_DEFINITION_ID", "NOT_A_DEFINITION")
    with pytest.raises(ValueError, match="未知 ENTRY 定义"):
        active_definition_id()
