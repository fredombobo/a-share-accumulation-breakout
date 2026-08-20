"""v2 错误码注册表测试：envelope 契约、未知码 fail-closed、机器快照。"""
from __future__ import annotations

import pytest

from ab_screener.domain.errors_v2 import (
    ERROR_CODES,
    V2Error,
    assert_code_registered,
    error_codes_manifest,
)


def test_envelope_shape_and_defaults():
    e = V2Error("VALIDATION_FAILED", details={"field": "top_n"})
    env = e.to_envelope()
    assert set(env) == {"code", "message", "details", "retryable", "request_id"}
    assert env["code"] == "VALIDATION_FAILED"
    assert env["message"]  # 非空人话
    assert env["details"] == {"field": "top_n"}
    assert env["retryable"] is False
    assert env["request_id"] == ""


def test_unknown_code_rejected_fail_closed():
    with pytest.raises(ValueError, match="未注册"):
        V2Error("NOT_A_REAL_CODE")


def test_code_override_and_retryable_override():
    e = V2Error("DB_QUICK_CHECK_FAILED", message="自定义消息", retryable=True)
    assert e.message == "自定义消息"
    assert e.retryable is True


def test_assert_code_registered():
    assert_code_registered("SCHEMA_INCOMPATIBLE")
    with pytest.raises(ValueError):
        assert_code_registered("MADE_UP")


def test_registry_manifest_serializable():
    m = error_codes_manifest()
    assert isinstance(m, dict) and len(m) >= 15
    for code, meta in m.items():
        assert code in ERROR_CODES
        assert "message" in meta and "retryable" in meta


def test_live_trading_code_present():
    # 硬门错误码必须存在（启动失败语义）
    assert "LIVE_TRADING_ENABLED" in ERROR_CODES
