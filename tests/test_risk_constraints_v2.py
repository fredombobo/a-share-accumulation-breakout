"""P5.1 风险约束测试：15 稳定拒绝码、卖出不被买入集中度误拦。"""
from __future__ import annotations

from ab_screener.domain.risk.constraints import (
    constraint_codes,
    evaluate_constraints,
)
from ab_screener.domain.risk.models import (
    RISK_CODES,
    OrderIntent,
    PortfolioState,
    Position,
    RiskConfig,
)


def _state(**over) -> PortfolioState:
    base = {
        "cash_fen": 90_000_000,           # 900 万
        "equity_fen": 100_000_000,        # 1000 万
        "positions": (
            Position(ts_code="000001.SZ", qty=1000, sellable_qty=1000,
                     latest_close_micro=10_000_000, industry="bank", theme="value",
                     corr_group="g1"),
        ),
        "today": "20260810", "trade_date": "20260810",
        "regime": "neutral", "data_fresh_as_of": "20260810",
    }
    base.update(over)
    return PortfolioState(**base)


def _order(**over) -> OrderIntent:
    base = {"ts_code": "600000.SH", "side": "BUY", "qty": 100,
            "price_micro": 10_000_000, "participation_bps": 500}
    base.update(over)
    return OrderIntent(**base)


CFG = RiskConfig()


def test_15_stable_rejection_codes():
    codes = constraint_codes()
    assert len(codes) == 15
    assert set(codes) == set(RISK_CODES)
    assert codes == list(RISK_CODES)  # 顺序稳定


def test_cash_insufficient():
    v = evaluate_constraints(_state(cash_fen=100), _order(qty=10000), CFG)
    assert any(x.code == "RISK_CASH_INSUFFICIENT" for x in v)


def test_lot_share():
    v = evaluate_constraints(_state(), _order(qty=150), CFG)
    assert any(x.code == "RISK_LOT_SHARE" for x in v)


def test_t1_sellable():
    v = evaluate_constraints(_state(), _order(side="SELL", qty=2000), CFG)
    assert any(x.code == "RISK_T1_SELLABLE" for x in v)


def test_single_name_limit():
    # 100 股 × 10 元 = 1000 元，与已有 1000 股×10 元 → 单票 2000 元 / 1000 万 = 0.02% 不过限
    # 用大额订单触限
    v = evaluate_constraints(_state(), _order(qty=2_000_000), CFG)  # 2 亿分 = 2000 万
    assert any(x.code == "RISK_SINGLE_NAME_LIMIT" for x in v)


def test_industry_theme_corr_limits():
    big = _order(qty=1_000_000)  # 1000 万买入银行股
    codes = {x.code for x in evaluate_constraints(_state(), big, CFG)}
    assert "RISK_INDUSTRY_LIMIT" in codes
    assert "RISK_THEME_LIMIT" in codes
    assert "RISK_CORRELATED_EXPOSURE" in codes


def test_position_count_and_total_position():
    many = tuple(
        Position(ts_code=f"{i:06d}.SZ", qty=100, sellable_qty=100,
                 latest_close_micro=10_000_000)
        for i in range(30)
    )
    v = evaluate_constraints(_state(positions=many), _order(), CFG)
    assert any(x.code == "RISK_POSITION_COUNT_LIMIT" for x in v)
    # 总仓：持仓 30×1000元 = 3 万 + 新单 1000 元，远低于 90%——用大额触限
    v2 = evaluate_constraints(_state(), _order(qty=9_000_000), CFG)
    assert any(x.code == "RISK_TOTAL_POSITION_LIMIT" for x in v2)


def test_min_cash():
    # 现金 100 万，最低 10%（100 万）→ 任何买入都触发
    v = evaluate_constraints(_state(cash_fen=10_000_000, equity_fen=100_000_000),
                             _order(qty=1000), CFG)
    assert any(x.code == "RISK_MIN_CASH" for x in v)


def test_daily_addition_and_participation():
    v = evaluate_constraints(_state(), _order(qty=2_000_000), CFG)
    assert any(x.code == "RISK_DAILY_ADDITION_LIMIT" for x in v)
    v2 = evaluate_constraints(_state(), _order(participation_bps=2000), CFG)
    assert any(x.code == "RISK_PARTICIPATION_LIMIT" for x in v2)


def test_defensive_regime_and_stale_data():
    v = evaluate_constraints(_state(regime="defensive"), _order(), CFG)
    assert any(x.code == "RISK_DEFENSIVE_REGIME" for x in v)
    v2 = evaluate_constraints(_state(data_fresh_as_of="20260801"), _order(), CFG)
    assert any(x.code == "RISK_STALE_DATA" for x in v2)


def test_price_deviation_stale_quote():
    v = evaluate_constraints(
        _state(data_fresh_as_of="2026-08-10T16:00:00+08:00"),
        _order(expected_quote_available_at="2026-08-09T10:00:00+08:00"), CFG)
    assert any(x.code == "RISK_STALE_DATA" for x in v)


def test_sell_not_blocked_by_buy_concentration():
    """卖出不被买入集中度规则错误拦截。"""
    big_sell = _order(side="SELL", qty=1000, price_micro=20_000_000)
    violations = evaluate_constraints(_state(), big_sell, CFG)
    buy_codes = {"RISK_SINGLE_NAME_LIMIT", "RISK_INDUSTRY_LIMIT", "RISK_THEME_LIMIT",
                 "RISK_CORRELATED_EXPOSURE", "RISK_POSITION_COUNT_LIMIT",
                 "RISK_TOTAL_POSITION_LIMIT", "RISK_MIN_CASH", "RISK_DAILY_ADDITION_LIMIT",
                 "RISK_CASH_INSUFFICIENT"}
    assert not (buy_codes & {x.code for x in violations})


def test_clean_order_no_violations():
    assert evaluate_constraints(_state(), _order(), CFG) == []
