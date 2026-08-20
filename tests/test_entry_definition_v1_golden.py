"""V1 golden 锁定测试：定义快照/语义哈希/代表性检测输出与 fixture 严格一致。

作用：V1 语义冻结——signals/entry_definition 未来任何改动若改变 V1 行为，
本测试立即失败（除非有意识升 V2 并更新 fixture）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ab_screener.domain.entry_definition import definition_snapshot
from ab_screener.domain.entry_registry import (
    active_definition_id,
    registered_definition_ids,
    resolve_definition,
    semantic_hash,
)
from signals import detect_accumulation_breakout
from test_signals import make_synthetic

FIXTURE = ROOT / "tests" / "fixtures" / "entry_v1_golden.json"
V1_ID = "A_POOL_STRICT_NEXT_OPEN_V1"


def _fixture() -> dict:
    assert FIXTURE.is_file(), "缺少 entry_v1_golden.json（运行生成脚本重建）"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_snapshot_stable_and_hashed():
    fx = _fixture()
    assert definition_snapshot() == fx["snapshot"]
    assert semantic_hash(V1_ID) == fx["semantic_hash"]


def test_v1_sample_output_frozen():
    """V1 代表性检测输出必须与 fixture 逐字段一致（语义漂移即失败）。"""
    fx = _fixture()
    df = make_synthetic(seed=42, flat_days=80)
    sig = detect_accumulation_breakout(df)
    stable = {k: sig.get(k) for k in (
        "is_breakout", "box_days", "box_amp", "box_high", "box_low", "breakout_date",
        "breakout_vol_ratio", "breakout_pct_chg", "hold_pullbacks", "cond_ma60", "cond_position")}
    stable["breakout_date"] = str(stable["breakout_date"] or "")
    assert stable == fx["sample"]
    blob = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16] == fx["sample_hash"]


def test_registry_resolves_v1_and_v2():
    assert V1_ID in registered_definition_ids()
    assert "A_POOL_STRICT_NEXT_OPEN_V2" in registered_definition_ids()
    v1 = resolve_definition(V1_ID)
    assert v1["id"] == V1_ID
    v2 = resolve_definition("A_POOL_STRICT_NEXT_OPEN_V2")
    assert v2["base_on"] == V1_ID
    assert v2["semantic_deltas"]["require_ma60"] is True


def test_unknown_definition_fail_closed():
    with pytest.raises(ValueError, match="未知 ENTRY 定义"):
        resolve_definition("NOT_A_DEFINITION")


def test_active_default_is_v1():
    assert active_definition_id() == V1_ID
