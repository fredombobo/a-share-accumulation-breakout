"""v2 唯一 typed 配置：resolved config + feature flags + SHA-256。

契约（platform-contracts §1）：解析顺序 = 默认文件 configs/platform_v2.yaml →
明确环境变量 overlay（V2_*/DAILY_*/INSTITUTIONAL_* 白名单）→ 命令行允许项，
最后生成不可变 resolved payload 与 SHA-256。DB 不作为第二套配置事实源。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_FILE = ROOT / "configs" / "platform_v2.yaml"

# 允许的环境变量 overlay（其余环境变量一律忽略，防隐式配置漂移）
ALLOWED_ENV_KEYS = {
    "V2_PIT_READ_ENABLED",
    "V2_EXECUTION_DUAL_RUN_ENABLED",
    "V2_EXECUTION_WRITE_ENABLED",
    "V2_STRATEGY_REGISTRY_ENABLED",
    "V2_RISK_ENFORCEMENT_ENABLED",
    "DAILY_SCHEDULER_ENABLED",
    "INSTITUTIONAL_CONSOLE_V2_ENABLED",
    "LIVE_TRADING_ENABLED",
}

# 布尔标志默认值（开发默认；发布默认由 config 文件提供）
DEFAULT_FLAGS: dict[str, bool] = {
    "V2_PIT_READ_ENABLED": False,
    "V2_EXECUTION_DUAL_RUN_ENABLED": True,
    "V2_EXECUTION_WRITE_ENABLED": False,
    "V2_STRATEGY_REGISTRY_ENABLED": False,
    "V2_RISK_ENFORCEMENT_ENABLED": False,
    "DAILY_SCHEDULER_ENABLED": False,
    "INSTITUTIONAL_CONSOLE_V2_ENABLED": False,
    "LIVE_TRADING_ENABLED": False,
}


class PlatformConfigError(RuntimeError):
    pass


def _parse_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    raise PlatformConfigError(f"{key} 不是合法布尔: {value!r}")


def load_resolved_config(
    config_file: Path | None = None,
    env: dict[str, str] | None = None,
    live_trading_override: bool | None = None,
) -> dict[str, Any]:
    """生成不可变 resolved config。返回 {flags, source, resolved_hash, file_sha256}。

    - 硬门：LIVE_TRADING_ENABLED 解析为 true 时抛错（启动必须失败）。
    """
    env = dict(os.environ if env is None else env)
    cfg_file = config_file or DEFAULT_CONFIG_FILE

    flags: dict[str, bool] = dict(DEFAULT_FLAGS)
    file_sha256 = ""
    if cfg_file.is_file():
        import yaml

        raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        overrides = raw.get("flags") or {}
        if not isinstance(overrides, dict):
            raise PlatformConfigError("platform_v2.yaml 的 flags 必须是映射")
        for key, value in overrides.items():
            if key not in DEFAULT_FLAGS:
                raise PlatformConfigError(f"config 文件含未知 flag: {key}")
            flags[key] = _parse_bool(value, key)
        file_sha256 = hashlib.sha256(cfg_file.read_bytes()).hexdigest()
    else:
        raise PlatformConfigError(f"默认配置文件缺失: {cfg_file}")

    # 环境变量 overlay（仅白名单键）
    for key in ALLOWED_ENV_KEYS:
        if key in env:
            flags[key] = _parse_bool(env[key], key)

    # 显式命令行覆盖（例如测试）
    if live_trading_override is not None:
        flags["LIVE_TRADING_ENABLED"] = live_trading_override

    if flags["LIVE_TRADING_ENABLED"]:
        raise PlatformConfigError(
            "LIVE_TRADING_ENABLED 必须为 false；本项目不包含真实下单能力"
        )

    resolved = {"flags": dict(flags), "source": str(cfg_file), "file_sha256": file_sha256}
    resolved["resolved_hash"] = resolved_hash(resolved)
    return resolved


def resolved_hash(resolved: dict[str, Any]) -> str:
    payload = {k: v for k, v in resolved.items() if k != "resolved_hash"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def flag_enabled(resolved: dict[str, Any], flag: str) -> bool:
    if flag not in DEFAULT_FLAGS:
        raise PlatformConfigError(f"未知 flag: {flag}")
    return bool((resolved.get("flags") or {}).get(flag, DEFAULT_FLAGS[flag]))
