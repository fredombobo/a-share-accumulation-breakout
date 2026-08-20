"""P6.4 持仓同步时间语义测试：字段拆分、旧字段保守映射、stale 阻断。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ab_screener.integrations.holdings_state import (
    HoldingsStateError,
    holdings_state,
)


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_fresh_state_fields_split(tmp_path: Path):
    state_file = tmp_path / "holdings_sync_state.json"
    _write(state_file, {
        "poll_attempted_at": "2026-08-10T15:30:00+08:00",
        "source_snapshot_at": "2026-08-10T15:00:00+08:00",
        "last_successful_sync_at": "2026-08-10T15:29:00+08:00",
        "cache_restored_at": "2026-08-10T15:31:00+08:00",
        "updated_at": "2026-08-10T15:31:00+08:00",
    })
    state = holdings_state(state_file)
    assert state["last_successful_sync_at"] == "2026-08-10T15:29:00+08:00"
    assert state["stale_local_cache"] is False
    assert state["ready"] is True


def test_failed_poll_does_not_update_success(tmp_path: Path):
    state_file = tmp_path / "holdings_sync_state.json"
    # 轮询尝试了但未成功 → 无 last_successful_sync_at
    _write(state_file, {
        "poll_attempted_at": "2026-08-10T15:30:00+08:00",
        "updated_at": "2026-08-10T15:30:00+08:00",
    })
    state = holdings_state(state_file)
    assert state["last_successful_sync_at"] is None
    assert state["stale_local_cache"] is True
    assert state["ready"] is False


def test_legacy_synced_at_maps_to_unknown_stale(tmp_path: Path):
    state_file = tmp_path / "holdings_sync_state.json"
    _write(state_file, {"synced_at": "2026-08-10T15:29:00+08:00",
                        "updated_at": "2026-08-10T15:31:00+08:00"})
    state = holdings_state(state_file)
    assert state["last_successful_sync_at"] == "UNKNOWN_LEGACY"
    assert state["stale_local_cache"] is True  # 无法证明 → 阻断就绪
    assert state["ready"] is False
    assert "O 闸门不得 PASS" in state["note"]


def test_missing_state_file_fail_closed(tmp_path: Path):
    with pytest.raises(HoldingsStateError, match="不存在"):
        holdings_state(tmp_path / "nope.json")
