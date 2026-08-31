"""T04 龙虎榜标准化：双榜去重、多原因、累计窗、指纹确定性。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ab_screener.application.lhb_transform import transform_day
from ab_screener.domain.lhb_contracts import AmountUnit
from ab_screener.domain.lhb_normalization import classify_reason, flow_fingerprint, period_for_hit

FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "lhb" / "duplicate_cases.json").read_text(
        encoding="utf-8"
    )
)
TS = FIXTURE["available_at"]
DAY = FIXTURE["disclose_date"]
CAL = FIXTURE["calendar"]
REASON_FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "lhb" / "reason_cases_20260803.json").read_text(
        encoding="utf-8"
    )
)


def _run(case: str, *, calendar=None, shuffle=False):
    block = FIXTURE[case]
    top_list = list(block["top_list"])
    top_inst = list(block["top_inst"])
    if shuffle:
        top_list = list(reversed(top_list))
        top_inst = list(reversed(top_inst))
    return transform_day(
        disclose_date=DAY,
        top_list_rows=top_list,
        top_inst_rows=top_inst,
        available_at=TS,
        calendar=calendar,
        unit=AmountUnit.WAN_YUAN,
        top_list_unit=AmountUnit.WAN_YUAN,
    )


def test_dual_board_amount_once_two_ranks():
    out = _run("dual_board")
    assert len(out.trades) == 1
    assert out.trades[0].buy_fen == 8_000_000
    assert out.trades[0].sell_fen == 2_000_000
    assert out.trades[0].net_fen == 6_000_000
    assert sorted(r.side for r in out.ranks) == ["BUY", "SELL"]
    assert {r.rank_no for r in out.ranks} == {1}


def test_multi_reason_money_once_all_reason_tags():
    out = _run("multi_reason")
    codes = {e.key.reason_code for e in out.events}
    assert codes == {"PCT_DEV_UP_1D", "TURNOVER_1D"}
    fps = {e.flow_fingerprint for e in out.events}
    assert len(fps) == 1
    assert len(out.trades) == 1
    assert sum(t.net_fen for t in out.trades) == 1_000_000


def test_three_day_window_not_folded_into_disclose_day_flow():
    daily = _run("dual_board", calendar=CAL)
    cum = _run("cumulative_3d", calendar=CAL)
    assert daily.events[0].key.window_code == "D1"
    assert daily.events[0].period_start == DAY
    assert cum.events[0].key.window_code == "D3"
    assert cum.events[0].period_start == "20260806"
    assert cum.events[0].period_end == "20260810"
    assert daily.events[0].flow_fingerprint != cum.events[0].flow_fingerprint


def test_unresolved_window_does_not_guess_dates():
    hit = classify_reason("未知的奇怪口径XYZ")
    assert hit.window_code == "UNRESOLVED_WINDOW"
    window, start, end = period_for_hit(hit, DAY)
    assert window == "UNRESOLVED_WINDOW" and start is None and end is None
    three = classify_reason("连续三个交易日内，涨幅偏离值累计达到20%")
    window2, start2, _end2 = period_for_hit(three, DAY, calendar=None)
    assert window2 == "UNRESOLVED_WINDOW" and start2 is None


def test_fingerprint_stable_under_input_order():
    a = _run("multi_reason")
    b = _run("multi_reason", shuffle=True)
    assert [e.flow_fingerprint for e in a.events] == [e.flow_fingerprint for e in b.events]
    assert [e.key.event_id for e in a.events] == [e.key.event_id for e in b.events]


def test_flow_fingerprint_ignores_reason_text_for_same_window():
    seats = [("机构专用", 100, 0)]
    a = flow_fingerprint(
        ts_code="000002.SZ",
        window_code="D1",
        period_start=DAY,
        period_end=DAY,
        seat_legs=seats,
    )
    b = flow_fingerprint(
        ts_code="000002.SZ",
        window_code="D1",
        period_start=DAY,
        period_end=DAY,
        seat_legs=list(reversed(seats)),
        reason_raw="ignored",
    )
    assert a == b


def test_dual_board_two_rows_do_not_double_amount():
    """Tushare 买卖各一行且两行都带买卖额时，不得相加翻倍。"""
    out = transform_day(
        disclose_date=DAY,
        top_list_rows=[
            {
                "ts_code": "000001.SZ",
                "trade_date": DAY,
                "reason": "日涨幅偏离值达到7%",
                "net_amount": 6,
                "amount": 5000,
            }
        ],
        top_inst_rows=[
            {
                "ts_code": "000001.SZ",
                "trade_date": DAY,
                "exalter": "某证券深圳益田路营业部",
                "side": "0",
                "buy": 8,
                "sell": 8,
                "reason": "日涨幅偏离值达到7%",
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": DAY,
                "exalter": "某证券深圳益田路营业部",
                "side": "1",
                "buy": 8,
                "sell": 2,
                "reason": "日涨幅偏离值达到7%",
            },
        ],
        available_at=TS,
        calendar=CAL,
        unit=AmountUnit.WAN_YUAN,
        top_list_unit=AmountUnit.WAN_YUAN,
    )
    assert len(out.trades) == 1
    assert out.trades[0].buy_fen == 8_000_000
    assert out.trades[0].sell_fen == 2_000_000
    assert sorted(r.side for r in out.ranks) == ["BUY", "SELL"]


def test_d1_and_d3_seat_rows_are_not_mixed():
    out = transform_day(
        disclose_date=DAY,
        top_list_rows=[
            {"ts_code": "000003.SZ", "trade_date": DAY, "reason": "日涨幅偏离值达到7%", "net_amount": 8, "amount": 100},
            {
                "ts_code": "000003.SZ",
                "trade_date": DAY,
                "reason": "连续三个交易日内，涨幅偏离值累计达到20%",
                "net_amount": 50,
                "amount": 800,
            },
        ],
        top_inst_rows=[
            {
                "ts_code": "000003.SZ",
                "exalter": "单日席位",
                "side": "0",
                "buy": 8,
                "sell": 0,
                "reason": "日涨幅偏离值达到7%",
            },
            {
                "ts_code": "000003.SZ",
                "exalter": "三日席位",
                "side": "0",
                "buy": 50,
                "sell": 0,
                "reason": "连续三个交易日内，涨幅偏离值累计达到20%",
            },
        ],
        available_at=TS,
        calendar=CAL,
    )
    d1 = next(e for e in out.events if e.key.window_code == "D1")
    d3 = next(e for e in out.events if e.key.window_code == "D3")
    d1_seats = {t.seat_raw for t in out.trades if t.event_id == d1.key.event_id}
    d3_seats = {t.seat_raw for t in out.trades if t.event_id == d3.key.event_id}
    assert d1_seats == {"单日席位"}
    assert d3_seats == {"三日席位"}


def test_quality_check_is_explanatory_not_identity():
    out = _run("dual_board")
    assert out.quality
    item = next(iter(out.quality.values()))
    assert item["status"] in {"OK", "WARN", "UNRESOLVED"}
    assert "seat_net_yuan" in item


@pytest.mark.parametrize("case", REASON_FIXTURE)
def test_actual_exchange_reason_texts(case: dict[str, str]):
    hit = classify_reason(case["reason_raw"])
    assert (hit.reason_code, hit.window_code) == (case["reason_code"], case["window_code"])


def test_non_a_share_rows_are_filtered_before_event_creation():
    out = transform_day(
        disclose_date=DAY,
        top_list_rows=[
            {
                "ts_code": "113001.SH",
                "trade_date": DAY,
                "reason": "非上市首日的可转债日收盘价格涨幅达到15%",
                "net_amount": 100_000,
                "amount": 1_000_000,
            },
            {
                "ts_code": "600001.SH",
                "trade_date": DAY,
                "reason": "有价格涨跌幅限制的日收盘价格涨幅达到15%的前五只证券",
                "net_amount": 100_000,
                "amount": 1_000_000,
            },
        ],
        top_inst_rows=[],
        available_at=TS,
        calendar=CAL,
    )
    assert [event.key.ts_code for event in out.events] == ["600001.SH"]
