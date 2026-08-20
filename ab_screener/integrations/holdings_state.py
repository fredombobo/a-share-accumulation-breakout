"""持仓同步时间语义只读适配器（P6.4）：读取父工作区 sync 状态，不得写父目录。

字段拆分为 poll_attempted_at / source_snapshot_at / last_successful_sync_at /
cache_restored_at / updated_at。失败轮询或恢复缓存不得更新成功同步时间；
stale_local_cache 明确阻断相关就绪状态。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PARENT_STATE = Path(r"E:\CODEX\Stock_selection\runtime\holdings_sync_state.json")


class HoldingsStateError(RuntimeError):
    """持仓状态读取错误（fail-closed）。"""


def _read_state(path: Path = _PARENT_STATE) -> dict[str, Any]:
    if not path.is_file():
        raise HoldingsStateError(f"持仓同步状态文件不存在: {path}（只读，不写父目录）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HoldingsStateError(f"持仓同步状态解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise HoldingsStateError("持仓同步状态格式非法（非对象）")
    return data


def holdings_state(path: Path = _PARENT_STATE) -> dict[str, Any]:
    """拆分时间语义；旧版单字段 synced_at 保守映射为 unknown/stale。"""
    data = _read_state(path)
    legacy_synced = data.get("synced_at") or data.get("last_sync_at")
    polled = data.get("poll_attempted_at")
    source = data.get("source_snapshot_at")
    success = data.get("last_successful_sync_at")
    cache_restored = data.get("cache_restored_at")

    # 旧字段保守映射：无法证明成功同步 → unknown/stale（O 闸门不能 PASS）
    if success is None and legacy_synced:
        success = "UNKNOWN_LEGACY"
        cache_restored = data.get("updated_at")
    fresh = success not in (None, "UNKNOWN_LEGACY") and source is not None
    return {
        "poll_attempted_at": polled,
        "source_snapshot_at": source,
        "last_successful_sync_at": success,
        "cache_restored_at": cache_restored,
        "updated_at": data.get("updated_at"),
        "stale_local_cache": not fresh,
        "ready": bool(fresh),
        "note": (
            "成功同步时间不可证明（旧字段或缺失）→ 保守 unknown/stale；O 闸门不得 PASS"
            if not fresh else ""
        ),
    }
