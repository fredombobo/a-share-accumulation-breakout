"""T03 跨源对账：保留双方原值，不覆盖；门禁 INSUFFICIENT 阻断 confirmed。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.application.lhb_reconcile import persist_diffs, quality_gate, reconcile_sources
from ab_screener.data.migration_intents.lhb_tracking_v2 import apply_lhb_tracking
from ab_screener.domain.lhb_contracts import AmountUnit


def test_reconcile_keeps_both_values_and_does_not_overwrite():
    left = [
        {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "日涨幅偏离值达到7%", "buy": 10, "sell": 0, "net_buy": 10},
        {"ts_code": "000002.SZ", "exalter": "某营业部", "reason": "日换手率达到20%", "buy": 5, "sell": 1, "net_buy": 4},
    ]
    right = [
        {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "日涨幅偏离值达到7%", "buy": 8, "sell": 0, "net_buy": 8},
        {"ts_code": "000003.SZ", "exalter": "总部", "reason": "日涨幅偏离值达到7%", "buy": 1, "sell": 0, "net_buy": 1},
    ]
    diffs = reconcile_sources(
        left_rows=left, right_rows=right, left_source="tushare", right_source="official_sh",
        trade_date="20260810",
        left_unit=AmountUnit.YUAN,
        right_unit=AmountUnit.YUAN,
    )
    types = {d["diff_type"] for d in diffs}
    assert "AMOUNT" in types
    assert "MISSING_LEFT" in types
    assert "MISSING_RIGHT" in types
    amount = next(d for d in diffs if d["diff_type"] == "AMOUNT" and d["field_name"] == "buy")
    assert amount["left_value"] == "10" and amount["right_value"] == "8"
    assert amount["status"] == "OPEN"


def test_persist_diffs_append_only(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "recon.db"))
    try:
        apply_lhb_tracking(conn)
        diffs = reconcile_sources(
            left_rows=[{"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "buy": 1, "sell": 0, "net_buy": 1}],
            right_rows=[{"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "buy": 2, "sell": 0, "net_buy": 2}],
            left_source="tushare",
            right_source="official_sz",
            trade_date="20260810",
            left_unit=AmountUnit.YUAN,
            right_unit=AmountUnit.YUAN,
        )
        n = persist_diffs(conn, diffs, available_at="2026-08-10T16:00:00+08:00")
        assert n >= 1
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE lhb_reconciliation SET status='REJECTED'")
        conn.rollback()
        row = conn.execute(
            "SELECT left_value, right_value, status FROM lhb_reconciliation LIMIT 1"
        ).fetchone()
        assert row[2] == "OPEN"
        assert row[0] != row[1]
    finally:
        conn.close()


def test_reconcile_keeps_dual_board_sides_and_explicit_units():
    left = [
        {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "0", "buy": 10, "sell": 0, "net_buy": 10},
        {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "1", "buy": 0, "sell": 3, "net_buy": -3},
    ]
    right = [
        {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "0", "buy": 100000, "sell": 0, "net_buy": 100000},
        {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "1", "buy": 0, "sell": 40000, "net_buy": -40000},
    ]
    diffs = reconcile_sources(
        left_rows=left,
        right_rows=right,
        left_source="tushare",
        right_source="official_sh",
        trade_date="20260810",
        left_unit=AmountUnit.WAN_YUAN,
        right_unit=AmountUnit.YUAN,
    )
    # 买榜 10万元 == 100000元，无买额差异；卖榜 3万元 != 40000元
    sell_diffs = [d for d in diffs if d["field_name"] == "sell"]
    buy_diffs = [d for d in diffs if d["field_name"] == "buy"]
    assert buy_diffs == []
    assert len(sell_diffs) == 1
    assert {d["diff_type"] for d in diffs} <= {"AMOUNT"}


def test_persist_diffs_second_run_is_idempotent(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "recon2.db"))
    try:
        apply_lhb_tracking(conn)
        diffs = reconcile_sources(
            left_rows=[{"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "0", "buy": 1, "sell": 0, "net_buy": 1}],
            right_rows=[{"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "0", "buy": 2, "sell": 0, "net_buy": 2}],
            left_source="tushare",
            right_source="official_sz",
            trade_date="20260810",
            left_unit=AmountUnit.YUAN,
            right_unit=AmountUnit.YUAN,
        )
        first = persist_diffs(conn, diffs, available_at="2026-08-10T16:00:00+08:00")
        second = persist_diffs(conn, diffs, available_at="2026-08-10T16:00:00+08:00")
        assert first >= 1
        assert second == 0
        n = conn.execute("SELECT COUNT(*) FROM lhb_reconciliation").fetchone()[0]
        assert n == first
    finally:
        conn.close()


def test_changed_values_append_same_reconciliation_identity(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "changed.db"))
    try:
        apply_lhb_tracking(conn)
        common = {
            "left_rows": [
                {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "0", "buy": 1, "sell": 0, "net_buy": 1}
            ],
            "left_source": "tushare",
            "right_source": "official_sz",
            "trade_date": "20260810",
            "left_unit": AmountUnit.YUAN,
            "right_unit": AmountUnit.YUAN,
        }
        first = reconcile_sources(
            **common,
            right_rows=[
                {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "0", "buy": 2, "sell": 0, "net_buy": 2}
            ],
        )
        changed = reconcile_sources(
            **common,
            right_rows=[
                {"ts_code": "000001.SZ", "exalter": "机构专用", "reason": "r", "side": "0", "buy": 3, "sell": 0, "net_buy": 3}
            ],
        )
        persist_diffs(conn, first, available_at="2026-08-10T16:00:00+08:00")
        persist_diffs(conn, changed, available_at="2026-08-11T16:00:00+08:00")
        rows = conn.execute(
            "SELECT recon_id,revision,right_value FROM lhb_reconciliation WHERE field_name='buy' ORDER BY revision"
        ).fetchall()
        assert rows[0][0] == rows[1][0]
        assert rows == [(rows[0][0], 1, "2"), (rows[0][0], 2, "3")]
    finally:
        conn.close()


def test_same_amount_diffs_for_two_seats_keep_distinct_ids(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "locator.db"))
    try:
        apply_lhb_tracking(conn)
        left = [
            {"ts_code": "000001.SZ", "exalter": seat, "reason": "r", "side": "0", "buy": 1, "sell": 0, "net_buy": 1}
            for seat in ("席位甲", "席位乙")
        ]
        right = [dict(row, buy=2, net_buy=2) for row in left]
        diffs = reconcile_sources(
            left_rows=left,
            right_rows=right,
            left_source="tushare",
            right_source="official_sz",
            trade_date="20260810",
            left_unit=AmountUnit.YUAN,
            right_unit=AmountUnit.YUAN,
        )
        persist_diffs(conn, diffs, available_at="2026-08-10T16:00:00+08:00")
        ids = conn.execute(
            "SELECT recon_id FROM lhb_reconciliation WHERE field_name='buy'"
        ).fetchall()
        assert len(ids) == 2
        assert len({row[0] for row in ids}) == 2
    finally:
        conn.close()


def test_quality_gate_insufficient_blocks_confirmed():
    blocked = quality_gate(coverage_pct=50.0, recon_match_pct=99.0)
    assert blocked["result"] == "INSUFFICIENT"
    assert blocked["allows_confirmed_signal"] is False
    ok = quality_gate(coverage_pct=99.0, recon_match_pct=95.0)
    assert ok["result"] == "PASS"
    assert ok["allows_confirmed_signal"] is True
