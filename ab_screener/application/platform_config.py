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

ALLOWED_VALUE_ENV_KEYS = {
    "V2_AUTHORITATIVE_RESEARCH_RUN_ID": "authoritative_research_run_id",
    "V2_GATE_EVIDENCE_DIR": "gate_evidence_dir",
    "V2_SOAK_EVIDENCE_DIR": "soak_evidence_dir",
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

DEFAULT_EVIDENCE: dict[str, str] = {
    "authoritative_research_run_id": "",
    "gate_evidence_dir": "runtime/v2/gates",
    "soak_evidence_dir": "runtime/v2/soak",
}

# These controls are safety invariants rather than optional features.  They are
# reported by /platform/status and intentionally have no disabling flag.
HARD_GATES: tuple[str, ...] = (
    "FUNDS",
    "QUANTITY",
    "T_PLUS_ONE",
    "NO_SHORT",
    "POINT_IN_TIME",
    "RECONCILIATION",
    "LIVE_TRADING_DISABLED",
)

# Business endpoints protected by server-resolved flags.  Operational health,
# platform status and readiness stay readable even when the console is off.
_CONSOLE_PREFIXES = (
    "/api/v2/desk",
    "/api/v2/intelligence",
    "/api/v2/research",
    "/api/v2/review",
    "/api/v2/paper",
)


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
    """生成不可变 resolved config。返回 flags/evidence/source/hash。

    - 硬门：LIVE_TRADING_ENABLED 解析为 true 时抛错（启动必须失败）。
    """
    env = dict(os.environ if env is None else env)
    cfg_file = config_file or DEFAULT_CONFIG_FILE

    flags: dict[str, bool] = dict(DEFAULT_FLAGS)
    evidence: dict[str, str] = dict(DEFAULT_EVIDENCE)
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
        evidence_overrides = raw.get("evidence") or {}
        if not isinstance(evidence_overrides, dict):
            raise PlatformConfigError("platform_v2.yaml 的 evidence 必须是映射")
        unknown_evidence = set(evidence_overrides) - set(DEFAULT_EVIDENCE)
        if unknown_evidence:
            raise PlatformConfigError(
                f"config 文件含未知 evidence 配置: {sorted(unknown_evidence)}"
            )
        for key, value in evidence_overrides.items():
            if not isinstance(value, str):
                raise PlatformConfigError(f"evidence.{key} 必须是字符串")
            evidence[key] = value.strip()
        file_sha256 = hashlib.sha256(cfg_file.read_bytes()).hexdigest()
    else:
        raise PlatformConfigError(f"默认配置文件缺失: {cfg_file}")

    # 环境变量 overlay（仅白名单键）
    for key in ALLOWED_ENV_KEYS:
        if key in env:
            flags[key] = _parse_bool(env[key], key)
    for env_key, evidence_key in ALLOWED_VALUE_ENV_KEYS.items():
        if env_key in env:
            evidence[evidence_key] = str(env[env_key]).strip()

    # 显式命令行覆盖（例如测试）
    if live_trading_override is not None:
        flags["LIVE_TRADING_ENABLED"] = live_trading_override

    if flags["LIVE_TRADING_ENABLED"]:
        raise PlatformConfigError(
            "LIVE_TRADING_ENABLED 必须为 false；本项目不包含真实下单能力"
        )

    resolved = {
        "flags": dict(flags),
        "evidence": dict(evidence),
        "source": str(cfg_file),
        "file_sha256": file_sha256,
    }
    resolved["resolved_hash"] = resolved_hash(resolved)
    return resolved


def resolved_hash(resolved: dict[str, Any]) -> str:
    # Checkout location is deployment metadata, not configuration semantics.
    # Excluding it lets the same committed config retain one identity when a
    # release candidate moves from an integration worktree to the saved repo.
    payload = {
        k: v for k, v in resolved.items() if k not in {"resolved_hash", "source"}
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def flag_enabled(resolved: dict[str, Any], flag: str) -> bool:
    if flag not in DEFAULT_FLAGS:
        raise PlatformConfigError(f"未知 flag: {flag}")
    return bool((resolved.get("flags") or {}).get(flag, DEFAULT_FLAGS[flag]))


def required_flags_for_path(path: str) -> tuple[str, ...]:
    """Return server-side flags required for one v2 business path."""
    if path in {"/api/v2/platform/status", "/api/v2/readiness"}:
        return ()
    if path.startswith(_CONSOLE_PREFIXES) or path.startswith(
        ("/api/v2/strategies", "/api/v2/signals", "/api/v2/scan-profiles")
    ):
        return ("INSTITUTIONAL_CONSOLE_V2_ENABLED",)
    return ()
