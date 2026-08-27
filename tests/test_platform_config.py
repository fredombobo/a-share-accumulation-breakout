"""v2 platform_config 测试：resolved hash、env overlay、硬门、未知 flag。"""
from __future__ import annotations

import shutil

import pytest

from ab_screener.application.platform_config import (
    DEFAULT_FLAGS,
    PlatformConfigError,
    flag_enabled,
    load_resolved_config,
)


def test_defaults_and_resolved_hash_stable():
    a = load_resolved_config(env={})
    b = load_resolved_config(env={})
    assert a["resolved_hash"] == b["resolved_hash"]
    assert a["flags"]["LIVE_TRADING_ENABLED"] is False
    assert a["flags"]["V2_PIT_READ_ENABLED"] is False
    assert len(a["resolved_hash"]) == 16


def test_env_overlay_changes_hash_and_value():
    base = load_resolved_config(env={})
    over = load_resolved_config(env={"V2_PIT_READ_ENABLED": "true"})
    assert flag_enabled(over, "V2_PIT_READ_ENABLED") is True
    assert base["resolved_hash"] != over["resolved_hash"]


def test_unknown_env_key_ignored():
    r = load_resolved_config(env={"RANDOM_UNKNOWN": "1"})
    assert r["resolved_hash"] == load_resolved_config(env={})["resolved_hash"]


def test_live_trading_true_fails_startup():
    with pytest.raises(PlatformConfigError, match="LIVE_TRADING_ENABLED"):
        load_resolved_config(env={"LIVE_TRADING_ENABLED": "true"})
    with pytest.raises(PlatformConfigError):
        load_resolved_config(env={}, live_trading_override=True)


def test_flag_enabled_unknown_raises():
    r = load_resolved_config(env={})
    with pytest.raises(PlatformConfigError):
        flag_enabled(r, "NOT_A_FLAG")


def test_all_default_flags_covered():
    r = load_resolved_config(env={})
    assert set(DEFAULT_FLAGS) == set(r["flags"])


def test_resolved_hash_does_not_depend_on_checkout_path(tmp_path):
    first = tmp_path / "a" / "platform.yaml"
    second = tmp_path / "b" / "platform.yaml"
    first.parent.mkdir()
    second.parent.mkdir()
    shutil.copyfile("configs/platform_v2.yaml", first)
    shutil.copyfile(first, second)
    assert load_resolved_config(first, env={})["resolved_hash"] == load_resolved_config(
        second, env={}
    )["resolved_hash"]
