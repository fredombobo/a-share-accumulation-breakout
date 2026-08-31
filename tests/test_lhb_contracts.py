"""T01 龙虎榜领域契约：日期、金额、状态、键和身份语言。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from ab_screener.application.platform_config import DEFAULT_FLAGS
from ab_screener.domain.lhb_contracts import (
    LHB_MAY_GENERATE_ORDERS,
    LHB_RESEARCH_ONLY,
    TZ_NAME,
    AmountUnit,
    LhbContractError,
    LhbEventKey,
    fen_to_yuan,
    identity_display,
    is_a_share_ts_code,
    materialize_seat_legs,
    normalize_top_inst_side,
    parse_enum,
    parse_trade_date,
    reject_certain_person_claim,
    require_available_at,
    resolve_period,
    source_status_allows_confirmed,
    to_fen,
    tushare_amount_unit,
    validate_manifest_row,
    validate_seat_amounts,
)


def test_tushare_side_zero_one_maps_to_buy_sell():
    assert normalize_top_inst_side("0") == "BUY"
    assert normalize_top_inst_side("1") == "SELL"
    assert normalize_top_inst_side(0) == "BUY"
    assert normalize_top_inst_side(1) == "SELL"
    assert normalize_top_inst_side("BUY") == "BUY"
    assert normalize_top_inst_side(None, buy=10, sell=0) == "BUY"


def test_live_trading_and_research_boundary_frozen():
    assert DEFAULT_FLAGS["LIVE_TRADING_ENABLED"] is False
    assert DEFAULT_FLAGS["V2_PIT_READ_ENABLED"] is False
    assert DEFAULT_FLAGS["DAILY_SCHEDULER_ENABLED"] is False
    assert LHB_RESEARCH_ONLY is True
    assert LHB_MAY_GENERATE_ORDERS is False


def test_illegal_dates_rejected():
    for bad in ("", "2026-08-10", "20261301", "20260230", "abcd", None, 20260810):
        with pytest.raises(LhbContractError, match="非法日期"):
            parse_trade_date(bad)
    assert parse_trade_date("20260810") == "20260810"


def test_available_at_normalized_to_shanghai():
    assert TZ_NAME == "Asia/Shanghai"
    assert require_available_at("2026-08-10T16:00:00") == "2026-08-10T16:00:00+08:00"
    assert require_available_at("2026-08-10T08:00:00Z") == "2026-08-10T16:00:00+08:00"
    with pytest.raises(LhbContractError, match="时间字段缺失"):
        require_available_at("")


def test_negative_amounts_and_net_mismatch():
    with pytest.raises(LhbContractError, match="负金额"):
        validate_seat_amounts(buy_fen=-1, sell_fen=0, net_fen=-1)
    with pytest.raises(LhbContractError, match="负金额"):
        validate_seat_amounts(buy_fen=0, sell_fen=-8, net_fen=8)
    with pytest.raises(LhbContractError, match="买卖净额不一致"):
        validate_seat_amounts(buy_fen=100, sell_fen=40, net_fen=50)
    validate_seat_amounts(buy_fen=100, sell_fen=40, net_fen=60)


def test_unknown_status_rejected():
    with pytest.raises(LhbContractError, match="未知状态"):
        parse_enum("SUCCESS", ("COMPLETE",), label="source_status")
    with pytest.raises(LhbContractError, match="未知状态"):
        parse_enum("EMPTY", ("VALID_EMPTY",), label="source_status")
    assert source_status_allows_confirmed("COMPLETE") is True
    assert source_status_allows_confirmed("DEGRADED") is False
    assert source_status_allows_confirmed("VALID_EMPTY") is False


def test_manifest_status_does_not_disguise_empty_success():
    validate_manifest_row(source_status="VALID_EMPTY", row_count=0)
    with pytest.raises(LhbContractError, match="VALID_EMPTY"):
        validate_manifest_row(source_status="VALID_EMPTY", row_count=3)
    with pytest.raises(LhbContractError, match="COMPLETE"):
        validate_manifest_row(source_status="COMPLETE", row_count=0)
    with pytest.raises(LhbContractError, match="FETCH_FAILED"):
        validate_manifest_row(source_status="FETCH_FAILED", row_count=2)


def test_duplicate_event_keys_rejected():
    a = LhbEventKey(
        exchange="SZ",
        ts_code="000001.SZ",
        window_code="D1",
        reason_code="PCT_DEV_UP_1D",
        disclose_date="20260810",
    )
    b = LhbEventKey(
        exchange="SZ",
        ts_code="000001.SZ",
        window_code="D1",
        reason_code="PCT_DEV_UP_1D",
        disclose_date="20260810",
    )
    assert a.event_id == b.event_id
    from ab_screener.domain.lhb_contracts import assert_unique_event_ids

    with pytest.raises(LhbContractError, match="重复键"):
        assert_unique_event_ids([a.event_id, b.event_id])


def test_unresolved_window_allows_known_reason_code():
    key = LhbEventKey(
        exchange="SZ",
        ts_code="000001.SZ",
        window_code="UNRESOLVED_WINDOW",
        reason_code="PCT_DEV_UP_3D",
        disclose_date="20260810",
    )
    assert key.window_code == "UNRESOLVED_WINDOW"


def test_same_stock_day_multiple_reasons_and_windows_are_distinct():
    d1 = LhbEventKey(
        exchange="SZ",
        ts_code="000001.SZ",
        window_code="D1",
        reason_code="PCT_DEV_UP_1D",
        disclose_date="20260810",
    )
    turnover = LhbEventKey(
        exchange="SZ",
        ts_code="000001.SZ",
        window_code="D1",
        reason_code="TURNOVER_1D",
        disclose_date="20260810",
    )
    d3 = LhbEventKey(
        exchange="SZ",
        ts_code="000001.SZ",
        window_code="D3",
        reason_code="PCT_DEV_UP_3D",
        disclose_date="20260810",
    )
    assert len({d1.event_id, turnover.event_id, d3.event_id}) == 3


def test_amount_unit_conversion_to_yuan():
    assert to_fen("1.5", AmountUnit.WAN_YUAN) == 1_500_000
    assert to_fen("15000", AmountUnit.YUAN) == 1_500_000
    assert fen_to_yuan(1_500_000) == Decimal("15000.00")
    with pytest.raises(LhbContractError, match="金额精度超过分"):
        to_fen("0.001", AmountUnit.FEN)


def test_tushare_lhb_amount_units_are_explicitly_yuan():
    for dataset, fields in {
        "top_list": ("amount", "l_sell", "l_buy", "l_amount", "net_amount"),
        "top_inst": ("buy", "sell", "net_buy"),
    }.items():
        assert all(tushare_amount_unit(dataset, field) is AmountUnit.YUAN for field in fields)
    with pytest.raises(LhbContractError, match="未声明"):
        tushare_amount_unit("top_inst", "mystery")


def test_a_share_universe_excludes_bonds_funds_and_b_shares():
    assert all(is_a_share_ts_code(code) for code in ("600000.SH", "000001.SZ", "300750.SZ", "920001.BJ"))
    assert not any(
        is_a_share_ts_code(code)
        for code in ("110001.SH", "113001.SH", "159001.SZ", "200001.SZ", "900901.SH")
    )


def test_dual_board_amount_counted_once_ranks_kept():
    legs = materialize_seat_legs(
        event_id="evt1",
        seat_raw="某证券深圳益田路营业部",
        buy_amount="8",
        sell_amount="2",
        unit=AmountUnit.WAN_YUAN,
        buy_rank=1,
        sell_rank=4,
        available_at="2026-08-10T16:00:00+08:00",
        source="tushare",
    )
    assert legs.trade.buy_fen == 8_000_000
    assert legs.trade.sell_fen == 2_000_000
    assert legs.trade.net_fen == 6_000_000
    assert fen_to_yuan(legs.trade.net_fen) == Decimal("60000.00")
    assert [item.side for item in legs.ranks] == ["BUY", "SELL"]
    assert [item.rank_no for item in legs.ranks] == [1, 4]


def test_unresolved_window_does_not_guess_dates():
    assert resolve_period("UNRESOLVED_WINDOW", "20260810") == (None, None)
    with pytest.raises(LhbContractError, match="不得猜测日期"):
        resolve_period("UNRESOLVED_WINDOW", "20260810", period_start="20260808")
    with pytest.raises(LhbContractError, match="不得猜测日期"):
        resolve_period("D3", "20260810")
    assert resolve_period("D1", "20260810") == ("20260810", "20260810")


def test_identity_language_is_hypothesis_not_person():
    assert identity_display(
        actor_type="INSTITUTION_CHANNEL", label="某公募", evidence_grade="A"
    ) == "机构专用通道"
    assert identity_display(
        actor_type="CONNECT_CHANNEL", label="外资", evidence_grade="A"
    ) == "沪深股通聚合通道"
    assert "疑似" in identity_display(
        actor_type="HOT_MONEY_CANDIDATE", label="知名游资", evidence_grade="B"
    )
    with pytest.raises(LhbContractError, match="A 级"):
        identity_display(
            actor_type="HOT_MONEY_CANDIDATE", label="知名游资", evidence_grade="A"
        )
    with pytest.raises(LhbContractError, match="自然人"):
        reject_certain_person_claim("确定为某自然人")
