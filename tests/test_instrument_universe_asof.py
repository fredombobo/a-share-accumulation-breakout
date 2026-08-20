"""P1.2 instrument as-of 宇宙测试：生命周期、类型过滤、fail-closed。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.instrument_repository import (
    InstrumentMissingError,
    InstrumentRegistryError,
    get_instrument,
    load_from_csv,
    require_instrument,
    universe_asof,
    upsert_instrument,
)
from ab_screener.data.migration_intents.instrument_history_v2 import apply_instrument_rules
from ab_screener.data.migration_intents.pit_history_v2 import apply_pit_history
from ab_screener.domain.instrument import Instrument, classify_security, is_a_share_stock

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "universe_lifecycle.csv"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "inst.db"))
    apply_pit_history(c)
    apply_instrument_rules(c)
    yield c
    c.close()


def test_security_classification():
    assert classify_security("000001.SZ") == "stock"
    assert classify_security("600000.SH") == "stock"
    assert classify_security("300750.SZ") == "stock"
    assert classify_security("000300.SH") == "index"
    assert classify_security("399001.SZ") == "index"
    assert classify_security("510300.SH") == "etf"
    assert classify_security("500038.SH") == "fund"
    assert classify_security("113050.SH") == "bond"
    assert classify_security("830799.BJ") == "bse"
    assert classify_security("999999.XY") == "other"
    assert is_a_share_stock("000001.SZ") is True
    assert is_a_share_stock("000300.SH") is False


def test_instrument_lifecycle_validity():
    rule = Instrument(ts_code="600999.SH", name="x", exchange="SSE",
                      security_type="stock", list_date="20000101", delist_date="20230815")
    assert rule.is_active_at("20230101") is True
    assert rule.is_active_at("20230814") is True
    assert rule.is_active_at("20230815") is False   # 退市日当天起不进入宇宙
    assert rule.is_active_at("19991231") is False   # 上市前不进入
    assert rule.is_active_at("") is False


def test_instrument_validation_fail_closed():
    with pytest.raises(ValueError, match="ts_code"):
        Instrument(ts_code="", name="x", exchange="SSE", security_type="stock",
                   list_date="20000101")
    with pytest.raises(ValueError, match="list_date"):
        Instrument(ts_code="600000.SH", name="x", exchange="SSE", security_type="stock",
                   list_date="")
    with pytest.raises(ValueError, match="security_type"):
        Instrument(ts_code="600000.SH", name="x", exchange="SSE", security_type="",
                   list_date="20000101")
    with pytest.raises(ValueError, match="生命周期非法"):
        Instrument(ts_code="600000.SH", name="x", exchange="SSE", security_type="stock",
                   list_date="20230101", delist_date="20220101")


def test_universe_asof_excludes_index_etf_fund_bond_bse(conn):
    load_from_csv(conn, FIXTURE)
    codes = universe_asof(conn, "20240101")
    assert "000001.SZ" in codes and "600000.SH" in codes
    assert "000300.SH" not in codes   # 指数
    assert "399001.SZ" not in codes
    assert "510300.SH" not in codes   # ETF
    assert "511990.SH" not in codes   # 基金
    assert "113050.SH" not in codes   # 债券
    assert "830799.BJ" not in codes   # 北交所
    assert "600999.SH" not in codes   # 已退市


def test_universe_asof_lifecycle(conn):
    load_from_csv(conn, FIXTURE)
    # 上市前
    assert "600036.SH" not in universe_asof(conn, "20020408")
    # 上市日（含）
    assert "600036.SH" in universe_asof(conn, "20020409")
    # 退市股：2023-08-14 仍在（有效期内存在）
    codes_before = universe_asof(conn, "20230814")
    assert "600999.SH" in codes_before
    # 退市后消失
    assert "600999.SH" not in universe_asof(conn, "20230815")


def test_require_instrument_fail_closed(conn):
    load_from_csv(conn, FIXTURE)
    rule = require_instrument(conn, "000001.SZ", "20240101")
    assert rule.security_type == "stock"
    with pytest.raises(InstrumentMissingError, match="生命周期"):
        require_instrument(conn, "600999.SH", "20240101")  # 已退市
    with pytest.raises(InstrumentMissingError, match="类型"):
        require_instrument(conn, "000300.SH", "20240101")  # 指数
    with pytest.raises(InstrumentMissingError, match="缺少 instrument 规则"):
        require_instrument(conn, "600519.SH", "20240101")  # 无规则（不兜底）


def test_empty_registry_fail_closed(conn):
    with pytest.raises(InstrumentRegistryError, match="注册表为空"):
        universe_asof(conn, "20240101")


def test_upsert_projection_and_history(conn):
    rule = Instrument(ts_code="600519.SH", name="贵州茅台", exchange="SSE",
                      security_type="stock", list_date="20010827")
    upsert_instrument(conn, rule)
    got = get_instrument(conn, "600519.SH")
    assert got is not None and got.name == "贵州茅台" and got.list_date == "20010827"
    # 更新 → 投影 upsert + 历史追加
    upsert_instrument(conn, Instrument(ts_code="600519.SH", name="贵州茅台（改）", exchange="SSE",
                                       security_type="stock", list_date="20010827"))
    revs = conn.execute(
        "SELECT revision FROM instrument_lifecycle_history WHERE ts_code='600519.SH'"
    ).fetchall()
    assert sorted(r[0] for r in revs) == [1, 2]
    # 历史 append-only
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM instrument_lifecycle_history WHERE ts_code='600519.SH'")
    conn.rollback()


def test_migration_registered():
    from ab_screener.data.migration_registry import registered_ids

    assert "v2:instrument_rules" in registered_ids()
