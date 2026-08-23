"""快速健康接口：热路径不得执行 PRAGMA integrity_check / quick_check。"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _make_tiny_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS daily (ts_code TEXT, trade_date TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (id TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO schema_version VALUES ('schema_version', '101')")
        conn.execute("INSERT INTO daily VALUES ('000001.SZ', '20260807')")
        conn.commit()
    finally:
        conn.close()


def test_fast_health_returns_quick_fields_and_deep_check_status(tmp_path: Path) -> None:
    """快速健康返回 schema/latest date + 深检证书状态（PASS/STALE/MISSING）。"""
    from ab_screener.operations.health import system_health

    db = tmp_path / "tiny.db"
    _make_tiny_db(db)
    payload = system_health(db, backup_root=tmp_path)
    assert payload["database"]["deep_check"]["status"] in {"PASS", "STALE", "MISSING"}
    assert payload["database"]["latest_date"] == "20260807"
    assert payload["database"]["schema_version"] == "101"
    assert payload["database"]["fingerprint"]


def test_fast_health_never_runs_full_integrity_check(monkeypatch, tmp_path: Path) -> None:
    """RW-003：set_trace_callback 捕获全部 SQL；热路径出现 integrity_check/quick_check 即失败。"""
    from ab_screener.operations import health as health_mod

    db = tmp_path / "tiny2.db"
    _make_tiny_db(db)

    statements: list[str] = []
    original_connect = sqlite3.connect

    def spy_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        try:
            conn.set_trace_callback(lambda statement: statements.append(statement))
        except Exception:  # noqa: BLE001
            pass
        return conn

    monkeypatch.setattr(health_mod.sqlite3, "connect", spy_connect)

    payload = health_mod.system_health(db, backup_root=tmp_path)

    assert payload["database"]["deep_check"]["status"] in {"PASS", "STALE", "MISSING"}
    assert statements, "应至少捕获到快速检查的 SQL"
    forbidden = [
        s for s in statements
        if "integrity_check" in s.lower() or "quick_check" in s.lower()
    ]
    assert not forbidden, f"快速健康热路径执行了完整性检查: {forbidden}"
