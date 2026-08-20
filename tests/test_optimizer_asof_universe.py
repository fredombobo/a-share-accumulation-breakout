"""P1.2 optimizer.research_universe(as_of=...) fail-closed 测试（离线）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import local_store as local_store_mod
from ab_screener.data.instrument_repository import InstrumentRegistryError, upsert_instrument
from ab_screener.data.migration_registry import apply_pending
from ab_screener.domain.instrument import Instrument


@pytest.fixture()
def fresh_db(tmp_path: Path, monkeypatch) -> str:
    """空的 LocalStore 默认库（未迁移 v2 instrument 规则）。"""
    db = tmp_path / "u.db"
    monkeypatch.setattr(local_store_mod, "_DB_PATH", db)
    return str(db)


@pytest.fixture()
def migrated_db(tmp_path: Path, monkeypatch) -> str:
    """已迁移且含一条股票规则的库。"""
    db = tmp_path / "m.db"
    monkeypatch.setattr(local_store_mod, "_DB_PATH", db)
    # 先让 LocalStore 建立基础表
    from local_store import LocalStore

    LocalStore()
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)  # v2:pit_history + v2:instrument_rules
        upsert_instrument(
            conn,
            Instrument(ts_code="000001.SZ", name="平安银行", exchange="SZSE",
                       security_type="stock", list_date="19910403"),
        )
    finally:
        conn.close()
    return str(db)


def test_asof_universe_without_migration_fails(fresh_db: str):
    from optimizer import research_universe

    with pytest.raises(InstrumentRegistryError, match="instrument_universe_rules 表不存在"):
        research_universe(as_of="20240101")


@pytest.fixture()
def empty_registry_db(tmp_path: Path, monkeypatch) -> str:
    """已迁移 v2 但无任何 instrument 规则（未回填）。"""
    db = tmp_path / "empty.db"
    monkeypatch.setattr(local_store_mod, "_DB_PATH", db)
    from local_store import LocalStore

    LocalStore()
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()
    return str(db)


def test_asof_universe_empty_registry_fails(empty_registry_db: str):
    """表存在但无规则（未回填）→ fail-closed，绝不退化全市场。"""
    from optimizer import research_universe

    with pytest.raises(InstrumentRegistryError, match="注册表为空|宇宙为空"):
        research_universe(as_of="20240101")


def test_asof_universe_returns_lifecycle_valid_stocks(migrated_db: str):
    from optimizer import research_universe

    codes = research_universe(as_of="20240101")
    assert codes == ["000001.SZ"]


def test_asof_universe_excludes_out_of_lifecycle(migrated_db: str):
    import sqlite3

    from local_store import LocalStore

    conn = sqlite3.connect(str(LocalStore().db_path))
    try:
        upsert_instrument(
            conn,
            Instrument(ts_code="600999.SH", name="已退市", exchange="SSE",
                       security_type="stock", list_date="20000101", delist_date="20230101"),
        )
    finally:
        conn.close()
    from optimizer import research_universe

    # 退市日后 → 只剩 000001.SZ
    assert research_universe(as_of="20230601") == ["000001.SZ"]
    # 有效期内 → 两只
    assert set(research_universe(as_of="20220101")) == {"000001.SZ", "600999.SH"}
