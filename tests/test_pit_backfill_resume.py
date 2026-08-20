"""P1.1 回填测试：分块、checkpoint 断点续跑、覆盖率门禁（全部离线 fake pro）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from ab_screener.application.pit_backfill import PitBackfill
from ab_screener.data.migration_intents.aux_history_v2 import apply_aux_history
from ab_screener.data.migration_intents.pit_history_v2 import apply_pit_history


class FakePro:
    """离线 fake：trade_cal + daily/daily_basic 只返回两天的少量行。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def trade_cal(self, **kwargs):
        return pd.DataFrame(
            {
                "cal_date": ["20260810", "20260811", "20260812"],
                "is_open": [1, 1, 0],
            }
        )

    def daily(self, **kwargs):
        self.calls.append(f"daily:{kwargs.get('trade_date')}")
        d = kwargs["trade_date"]
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "trade_date": [d, d],
                "close": [10.0, 5.0],
            }
        )

    def daily_basic(self, **kwargs):
        self.calls.append(f"daily_basic:{kwargs.get('trade_date')}")
        d = kwargs["trade_date"]
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "trade_date": [d, d],
                "pe": [8.0, 9.0],
            }
        )

    def stock_basic(self):
        self.calls.append("stock_basic:ALL")
        return pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["平安银行"], "list_date": ["19910403"]}
        )

    def top10_holders(self, **kwargs):
        self.calls.append(f"top10_holders:{kwargs.get('ts_code')}")
        code = kwargs["ts_code"]
        return pd.DataFrame(
            {"ts_code": [code], "end_date": ["20260630"], "holder_name": ["某股东"]}
        )

    def fina_indicator(self, **kwargs):
        self.calls.append(f"fina_indicator:{kwargs.get('ts_code')}")
        code = kwargs["ts_code"]
        return pd.DataFrame(
            {"ts_code": [code], "ann_date": ["20260425"], "end_date": ["20260331"], "eps": [1.2]}
        )


@pytest.fixture()
def db(tmp_path: Path) -> str:
    path = tmp_path / "backfill.db"
    conn = sqlite3.connect(str(path))
    try:
        apply_pit_history(conn)
    finally:
        conn.close()
    return str(path)


def test_run_daily_family_partitions(db: str):
    bf = PitBackfill(db, pro=FakePro())
    # daily 族按交易日分区：trade_cal 开市日 20260810/20260811
    result = bf.run(["daily"], start="20260810", end="20260812")
    assert result["partitions_done"] == 2
    assert result["rows"] == 4
    assert result["skipped"] == 0
    assert result["per_dataset"]["daily"]["partitions_done"] == 2
    conn = sqlite3.connect(db)
    try:
        revs = conn.execute("SELECT DISTINCT revision FROM daily_history").fetchall()
        assert revs == [(1,)]
        total = conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0]
        assert total == 4
    finally:
        conn.close()


def test_resume_skips_done_partitions(db: str):
    bf = PitBackfill(db, pro=FakePro())
    first = bf.run(["daily"], start="20260810", end="20260812")
    assert first["partitions_done"] == 2
    second = bf.run(["daily"], start="20260810", end="20260812")
    assert second["skipped"] == 2 and second["partitions_done"] == 0
    # 修订不重复追加
    conn = sqlite3.connect(db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0]
        assert total == 4
    finally:
        conn.close()


def test_resume_from_in_progress_checkpoint(db: str):
    # 预置一个 in_progress checkpoint（模拟上次中断未完成）
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO pit_backfill_checkpoints (dataset, partition_key, status,"
            " last_key, row_count, source_hash, updated_at)"
            " VALUES ('daily','20260810','in_progress','20260810',0,'', '2026-08-12T00:00:00+08:00')"
        )
        conn.commit()
    finally:
        conn.close()
    bf = PitBackfill(db, pro=FakePro())
    result = bf.run(["daily"], start="20260810", end="20260812")
    assert result["partitions_done"] == 2  # in_progress 分区被重跑
    conn = sqlite3.connect(db)
    try:
        cp = conn.execute(
            "SELECT status, row_count FROM pit_backfill_checkpoints"
            " WHERE dataset='daily' AND partition_key='20260810'"
        ).fetchone()
        assert cp == ("done", 2)
    finally:
        conn.close()


def test_coverage_gate(db: str):
    bf = PitBackfill(db, pro=FakePro())
    before = bf.coverage_report(["daily"])
    assert before["all_done"] is False
    bf.run(["daily"], start="20260810", end="20260812")
    after = bf.coverage_report(["daily"])
    assert after["all_done"] is True
    assert after["daily"]["done"] == 2


def test_stock_basic_single_partition(db: str):
    bf = PitBackfill(db, pro=FakePro())
    result = bf.run(["stock_basic"], partitions={"stock_basic": ["ALL"]})
    assert result["partitions_done"] == 1
    assert result["rows"] == 1


def test_holder_plan_partitions_by_ts_code(db: str):
    """holder 分区键 = stock_basic/delisted_basic 的 ts_code（含退市）。"""
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO stock_basic (ts_code, name) VALUES (?,?)",
            [("000001.SZ", "a"), ("600000.SH", "b")],
        )
        conn.execute("CREATE TABLE delisted_basic (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO delisted_basic (ts_code, name) VALUES ('300001.SZ','c')")
        conn.commit()
    finally:
        conn.close()
    bf = PitBackfill(db, pro=FakePro())
    plan = bf._plan(["holder"], start=None, end=None, partitions=None)
    assert plan["holder"] == ["000001.SZ", "300001.SZ", "600000.SH"]
    # 无同步表时明确报错（不静默空跑）
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE stock_basic")
        conn.execute("DROP TABLE delisted_basic")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError, match="stock_basic"):
        bf._plan(["holder"], start=None, end=None, partitions=None)


def test_aux_daily_family_partitions_and_write(db: str):
    """top_list/margin/cyq 按交易日分区；holder 按 ts_code 拉取并写入 PIT 表。"""
    conn = sqlite3.connect(db)
    try:
        apply_aux_history(conn)
        conn.execute("CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO stock_basic (ts_code, name) VALUES ('000001.SZ','a')")
        conn.commit()
    finally:
        conn.close()
    bf = PitBackfill(db, pro=FakePro())
    plan = bf._plan(["top_list", "margin", "cyq"], start="20260810", end="20260812", partitions=None)
    assert plan["top_list"] == ["20260810", "20260811"]
    assert plan["margin"] == ["20260810", "20260811"]
    assert plan["cyq"] == ["20260810", "20260811"]
    holder_plan = bf._plan(["holder"], start=None, end=None, partitions=None)
    result = bf.run(["holder"], partitions=holder_plan)
    assert result["partitions_done"] == 1
    conn = sqlite3.connect(db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM holder_history").fetchone()[0]
        assert total == 1
    finally:
        conn.close()


def test_fina_indicator_partitions_by_ts_code(db: str):
    """fina_indicator 分区键 = ts_code（镜像网关不支持纯 period 查询）。"""
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO stock_basic (ts_code, name) VALUES ('000001.SZ','a')")
        conn.commit()
    finally:
        conn.close()
    bf = PitBackfill(db, pro=FakePro())
    plan = bf._plan(["fina_indicator"], start=None, end=None, partitions=None)
    assert plan["fina_indicator"] == ["000001.SZ"]
    result = bf.run(["fina_indicator"], partitions=plan)
    assert result["partitions_done"] == 1
    assert result["rows"] == 1
    assert bf._pro.calls == ["fina_indicator:000001.SZ"]
    conn = sqlite3.connect(db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM fina_indicator_history").fetchone()[0]
        assert total == 1
    finally:
        conn.close()
