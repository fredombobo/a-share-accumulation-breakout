"""P4.2 测试：ScanProfile 版本化/活跃/run manifest。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.scan_profile_repository import (
    ScanProfileError,
    active_profiles,
    get_profile,
    record_funnel_run,
    save_profile,
)
from ab_screener.domain.scan_profile import (
    ScanProfile,
    profile_config_hash,
    profile_id_for,
)


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "sp.db"))
    apply_pending(c)
    yield c
    c.close()


def _profile(**over) -> ScanProfile:
    base = {
        "name": "six-form-v1", "version": "1.0",
        "strategy_ids": ("accumulation_breakout_v1", "platform_breakout_v1"),
        "configs": {"accumulation_breakout_v1": {"vol_ratio": 1.6},
                    "platform_breakout_v1": {"platform_days": 15}},
    }
    base.update(over)
    return ScanProfile(**base)


def test_profile_id_and_config_hash_deterministic():
    p1 = _profile()
    p2 = _profile()
    assert p1.profile_id == p2.profile_id
    assert p1.config_hash == profile_config_hash(p1.configs)
    assert p1.profile_id == profile_id_for("six-form-v1")
    # 不同配置 → 不同 hash
    other = _profile(configs={"accumulation_breakout_v1": {"vol_ratio": 2.0},
                              "platform_breakout_v1": {"platform_days": 15}})
    assert p1.config_hash != other.config_hash
    # 不同版本同 name → 同 profile_id（版本是分支）
    assert _profile(version="2.0").profile_id == p1.profile_id


def test_profile_validation():
    with pytest.raises(ValueError, match="name"):
        ScanProfile(name="", version="1", strategy_ids=("a",),
                    configs={"a": {}})
    with pytest.raises(ValueError, match="至少需要一个策略"):
        ScanProfile(name="x", version="1", strategy_ids=(), configs={})
    with pytest.raises(ValueError, match="configs"):
        ScanProfile(name="x", version="1", strategy_ids=("a",), configs={})


def test_save_and_get_versioned(conn):
    save_profile(conn, _profile(version="1.0"))
    save_profile(conn, _profile(version="2.0", status="ACTIVE"))
    latest = get_profile(conn, _profile().profile_id)
    assert latest.version == "2.0"
    v1 = get_profile(conn, _profile().profile_id, version="1.0")
    assert v1.version == "1.0"
    active = active_profiles(conn)
    assert [p.version for p in active] == ["2.0"]


def test_funnel_run_manifest_append_only(conn):
    profile = _profile()
    manifest = record_funnel_run(
        conn, profile=profile, input_hash="ih1",
        stages=["load", "detect"], result={"saved": 3},
    )
    assert manifest
    # append-only
    with pytest.raises(Exception, match="append-only"):
        conn.execute("UPDATE scan_funnel_runs SET status='X' WHERE run_manifest_id=?", (manifest,))
    conn.rollback()


def test_missing_table_fail_closed(tmp_path: Path):
    empty = sqlite3.connect(str(tmp_path / "naked.db"))
    try:
        with pytest.raises(ScanProfileError, match="表不存在"):
            save_profile(empty, _profile())
    finally:
        empty.close()
