"""按真实 Tushare 抽样形态验收：side 仅 '0'/'1'、同席位同原因多行、双榜。"""
from __future__ import annotations

from collections import defaultdict

from ab_screener.application.lhb_transform import transform_day
from ab_screener.data.adapters.tushare_pit import prepare_top_inst_records
from ab_screener.domain.lhb_contracts import normalize_top_inst_side

DAY = "20260803"
TS = "2026-08-03T16:00:00+08:00"
CAL = ["20260730", "20260731", "20260803"]
REASON_D1 = "日涨幅偏离值达到7%"
REASON_D3 = "连续三个交易日内，涨幅偏离值累计达到20%"


def _dual_board_rows(n: int = 88) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        name = f"双榜席位{i:03d}营业部"
        code = f"00000{i % 9}.SZ"
        rows.append(
            {
                "ts_code": code,
                "trade_date": DAY,
                "exalter": name,
                "side": "0",
                "buy": 80_000.0,
                "sell": 80_000.0,
                "net_buy": 0.0,
                "reason": REASON_D1,
            }
        )
        rows.append(
            {
                "ts_code": code,
                "trade_date": DAY,
                "exalter": name,
                "side": "1",
                "buy": 80_000.0,
                "sell": 20_000.0,
                "net_buy": 60_000.0,
                "reason": REASON_D1,
            }
        )
    return rows


def _same_side_dupes(n: int = 7) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        name = f"重复买席位{i:03d}营业部"
        row = {
            "ts_code": "000010.SZ",
            "trade_date": DAY,
            "exalter": name,
            "side": "0",
            "buy": 30_000.0,
            "sell": 0.0,
            "net_buy": 30_000.0,
            "reason": REASON_D1,
        }
        rows.extend([row, dict(row)])
    return rows


def test_real_sample_side_values_are_only_zero_one():
    rows = _dual_board_rows() + _same_side_dupes()
    raw_sides = {str(r["side"]) for r in rows}
    assert raw_sides == {"0", "1"}
    prepared = prepare_top_inst_records(rows)
    assert {r["side"] for r in prepared} == {"BUY", "SELL"}
    assert all(normalize_top_inst_side(r["side"]) in {"BUY", "SELL"} for r in rows)


def test_real_sample_duplicate_groups_and_dual_board_counts():
    rows = _dual_board_rows(88) + _same_side_dupes(7)
    groups: dict[tuple[str, str, str], list] = defaultdict(list)
    for row in rows:
        groups[(row["ts_code"], row["exalter"], row["reason"])].append(row)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    dual = {
        k: v
        for k, v in multi.items()
        if {"0", "1"} <= {str(r["side"]) for r in v}
    }
    assert len(multi) == 95
    assert len(dual) == 88


def test_real_sample_transform_does_not_double_dual_board():
    inst = _dual_board_rows(88) + _same_side_dupes(7)
    list_rows = []
    for ts_code in sorted({r["ts_code"] for r in inst}):
        list_rows.append(
            {"ts_code": ts_code, "trade_date": DAY, "reason": REASON_D1, "net_amount": 10_000, "amount": 1_000_000}
        )
    out = transform_day(
        disclose_date=DAY,
        top_list_rows=list_rows,
        top_inst_rows=inst,
        available_at=TS,
        calendar=CAL,
    )
    dual_trades = [t for t in out.trades if t.seat_raw.startswith("双榜席位")]
    assert len(dual_trades) == 88
    assert all(t.buy_fen == 8_000_000 and t.sell_fen == 2_000_000 for t in dual_trades)
    dup_trades = [t for t in out.trades if t.seat_raw.startswith("重复买席位")]
    assert len(dup_trades) == 7
    assert all(t.buy_fen == 3_000_000 and t.sell_fen == 0 for t in dup_trades)


def test_real_sample_d1_and_d3_reasons_stay_separated():
    inst = [
        {
            "ts_code": "000001.SZ",
            "exalter": "单日席位A",
            "side": "0",
            "buy": 80_000,
            "sell": 0,
            "reason": REASON_D1,
        },
        {
            "ts_code": "000001.SZ",
            "exalter": "三日席位B",
            "side": "0",
            "buy": 500_000,
            "sell": 0,
            "reason": REASON_D3,
        },
    ]
    out = transform_day(
        disclose_date=DAY,
        top_list_rows=[
            {"ts_code": "000001.SZ", "reason": REASON_D1, "net_amount": 80_000, "amount": 1_000_000},
            {"ts_code": "000001.SZ", "reason": REASON_D3, "net_amount": 500_000, "amount": 8_000_000},
        ],
        top_inst_rows=inst,
        available_at=TS,
        calendar=CAL,
    )
    d1 = next(e for e in out.events if e.key.window_code == "D1")
    d3 = next(e for e in out.events if e.key.window_code == "D3")
    d1_seats = {t.seat_raw for t in out.trades if t.event_id == d1.key.event_id}
    d3_seats = {t.seat_raw for t in out.trades if t.event_id == d3.key.event_id}
    assert d1_seats == {"单日席位A"}
    assert d3_seats == {"三日席位B"}


def test_real_yuan_magnitude_is_not_multiplied_by_ten_thousand():
    raw_yuan = 1_013_162_595.79
    out = transform_day(
        disclose_date=DAY,
        top_list_rows=[
            {
                "ts_code": "600001.SH",
                "trade_date": DAY,
                "reason": REASON_D1,
                "net_amount": raw_yuan,
                "amount": 2_000_000_000,
            }
        ],
        top_inst_rows=[
            {
                "ts_code": "600001.SH",
                "trade_date": DAY,
                "exalter": "机构专用",
                "side": "0",
                "buy": raw_yuan,
                "sell": 0,
                "net_buy": raw_yuan,
                "reason": REASON_D1,
            }
        ],
        available_at=TS,
        calendar=CAL,
    )
    assert out.trades[0].buy_fen == 101_316_259_579
