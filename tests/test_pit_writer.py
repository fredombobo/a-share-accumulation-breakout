"""P1.1 PIT 写测试：revision 分配、append-only 硬约束、预算、清单。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_intents.pit_history_v2 import apply_pit_history
from ab_screener.data.pit_writer import MAX_ROWS_PER_TX, build_records, write_plain


@pytest.fixture()
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "pit.db"))
    apply_pit_history(c)
    yield c
    c.close()


def test_revision_increments_per_business_key(conn):
    write_plain(
        conn, "daily",
        [{"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.0}],
        source="tushare", available_at="2026-08-10T16:00:00+08:00", partition_key="20260810",
    )
    write_plain(
        conn, "daily",
        [{"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.5}],
        source="tushare", available_at="2026-08-11T09:00:00+08:00", partition_key="20260810",
    )
    rows = conn.execute(
        "SELECT ts_code, trade_date, revision, content_hash FROM daily_history"
        " WHERE ts_code='000001.SZ' ORDER BY revision"
    ).fetchall()
    assert [(r[2], r[3][:16] != "") for r in rows] == [(1, True), (2, True)]


def test_append_only_triggers_block_update_and_delete(conn):
    write_plain(
        conn, "daily",
        [{"ts_code": "000001.SZ", "trade_date": "20260810", "close": 10.0}],
        source="tushare", available_at="2026-08-10T16:00:00+08:00", partition_key="20260810",
    )
    with pytest.raises(Exception, match="append-only"):
        conn.execute("UPDATE daily_history SET payload_json='{}' WHERE revision=1")
    conn.rollback()
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM daily_history WHERE revision=1")
    conn.rollback()


def test_chunk_over_budget_rejected(conn):
    records = build_records(
        "daily",
        [{"ts_code": f"{i:06d}.SZ", "trade_date": "20260810", "close": 1.0}
         for i in range(MAX_ROWS_PER_TX + 1)],
        source="tushare", available_at="2026-08-10T16:00:00+08:00", conn=conn,
    )
    from ab_screener.data.pit_writer import write_chunk

    with pytest.raises(ValueError, match="超过预算"):
        write_chunk(conn, "daily", records, partition_key="20260810",
                    source="tushare", available_at="2026-08-10T16:00:00+08:00")


def test_missing_business_key_rejected(conn):
    with pytest.raises(ValueError, match="业务键缺失"):
        build_records(
            "daily",
            [{"ts_code": "000001.SZ", "close": 10.0}],  # 缺 trade_date
            source="tushare", available_at="2026-08-10T16:00:00+08:00", conn=conn,
        )


def test_unknown_dataset_rejected(conn):
    with pytest.raises(ValueError, match="未知 PIT 数据集"):
        write_plain(conn, "nope", [{"a": 1}], source="tushare",
                    available_at="2026-08-10T16:00:00+08:00", partition_key="x")


def test_manifest_and_content_sha256(conn):
    write_plain(
        conn, "moneyflow",
        [{"ts_code": "000001.SZ", "trade_date": "20260810", "net_mf_amount": 123.0}],
        source="tushare", available_at="2026-08-10T16:00:00+08:00", partition_key="20260810",
    )
    row = conn.execute(
        "SELECT dataset, partition_key, row_count, content_sha256 FROM raw_ingest_manifests"
    ).fetchone()
    assert row[0] == "moneyflow" and row[1] == "20260810" and row[2] == 1
    assert len(row[3]) == 64


def test_duplicate_business_key_in_same_chunk_increments_revision(conn):
    """top_list 同日多 reason 会返回同业务键重复行：revision 递增，不触发 UNIQUE 冲突。"""
    from ab_screener.data.migration_intents.aux_history_v2 import apply_aux_history

    apply_aux_history(conn)
    write_plain(
        conn, "top_list",
        [
            {"ts_code": "000620.SZ", "trade_date": "20260818", "reason": "日涨幅偏离值达到7%"},
            {"ts_code": "000620.SZ", "trade_date": "20260818", "reason": "连续三日涨幅偏离值累计20%"},
        ],
        source="tushare", available_at="2026-08-18T16:00:00+08:00", partition_key="20260818",
    )
    rows = conn.execute(
        "SELECT ts_code, trade_date, revision, payload_json FROM top_list_history"
        " ORDER BY revision"
    ).fetchall()
    assert len(rows) == 2
    assert [r[2] for r in rows] == [1, 2]
    assert "日涨幅" in rows[0][3] and "连续三日" in rows[1][3]
