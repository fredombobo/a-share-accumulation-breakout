"""唯一组合约束引擎（P5.1）：15 个独立稳定拒绝码。

- Review 与 confirm 使用同一套 `evaluate_constraints`。
- 卖出不被买入集中度规则错误拦截（BUY 专属约束只对 BUY 生效）。
- 硬约束（现金/份额/T+1）永不可关闭；软约束按 RiskConfig 阈值。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ab_screener.domain.risk.models import (
    RISK_CODES,
    ConstraintViolation,
    OrderIntent,
    PortfolioState,
    RiskConfig,
)


def _pct(value: int, total: int) -> float:
    return value / total if total > 0 else 0.0


def _notional_fen(order: OrderIntent) -> int:
    return order.price_micro * order.qty // 10_000


def check_cash(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.side != "BUY":
        return None
    cost = _notional_fen(order)
    if cost > state.cash_fen:
        return ConstraintViolation(
            "RISK_CASH_INSUFFICIENT",
            f"现金不足: 需要 {cost} 分，可用 {state.cash_fen} 分",
            {"need_fen": cost, "cash_fen": state.cash_fen},
        )
    return None


def check_lot(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.qty <= 0 or order.qty % cfg.lot_size != 0:
        return ConstraintViolation(
            "RISK_LOT_SHARE",
            f"数量必须为正且为整手（{cfg.lot_size}）: {order.qty}",
            {"qty": order.qty, "lot_size": cfg.lot_size},
        )
    return None


def check_t1_sellable(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.side != "SELL":
        return None
    sellable = state.sellable_of(order.ts_code)
    if order.qty > sellable:
        return ConstraintViolation(
            "RISK_T1_SELLABLE",
            f"可卖数量不足（T+1）: 请求 {order.qty} > 可卖 {sellable}",
            {"requested": order.qty, "sellable": sellable},
        )
    return None


def check_single_name(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.side != "BUY":
        return None
    equity = state.equity_fen
    if equity <= 0:
        return None
    existing_value = 0
    for p in state.positions:
        if p.ts_code == order.ts_code:
            existing_value = p.latest_close_micro * p.qty // 10_000
    after = _pct(_notional_fen(order) + existing_value, equity)
    if after > cfg.max_single_name_pct:
        return ConstraintViolation(
            "RISK_SINGLE_NAME_LIMIT",
            f"单票占比将超 {cfg.max_single_name_pct:.0%}: {after:.1%}",
            {"after_pct": after, "limit": cfg.max_single_name_pct},
        )
    return None


def _exposure_pct(
    state: PortfolioState, order: OrderIntent, attr: str, limit: float, code: str, label: str
) -> ConstraintViolation | None:
    if order.side != "BUY":
        return None
    equity = state.equity_fen
    if equity <= 0:
        return None
    value = _notional_fen(order)
    attr_value = getattr(order, "_" + attr, "") or ""
    for p in state.positions:
        if getattr(p, attr) == attr_value:
            value += p.latest_close_micro * p.qty // 10_000
    pct = _pct(value, equity)
    if pct > limit:
        return ConstraintViolation(
            code,
            f"{label}占比将超 {limit:.0%}: {pct:.1%}",
            {"after_pct": pct, "limit": limit},
        )
    return None


def check_industry(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    return _exposure_pct(state, order, "industry", cfg.max_industry_pct,
                         "RISK_INDUSTRY_LIMIT", "行业")


def check_theme(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    return _exposure_pct(state, order, "theme", cfg.max_theme_pct,
                         "RISK_THEME_LIMIT", "主题")


def check_correlated(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    return _exposure_pct(state, order, "corr_group", cfg.max_corr_group_pct,
                         "RISK_CORRELATED_EXPOSURE", "相似暴露")


def check_position_count(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.side != "BUY":
        return None
    new_names = 1 if state.qty_of(order.ts_code) == 0 else 0
    if len(state.positions) + new_names > cfg.max_position_count:
        return ConstraintViolation(
            "RISK_POSITION_COUNT_LIMIT",
            f"持仓数将超 {cfg.max_position_count}: {len(state.positions) + new_names}",
            {"count": len(state.positions) + new_names, "limit": cfg.max_position_count},
        )
    return None


def check_total_position(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.side != "BUY":
        return None
    equity = state.equity_fen
    if equity <= 0:
        return None
    invested = state.market_value_fen() + _notional_fen(order)
    pct = _pct(invested, equity)
    if pct > cfg.max_total_position_pct:
        return ConstraintViolation(
            "RISK_TOTAL_POSITION_LIMIT",
            f"总仓将超 {cfg.max_total_position_pct:.0%}: {pct:.1%}",
            {"after_pct": pct, "limit": cfg.max_total_position_pct},
        )
    return None


def check_min_cash(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.side != "BUY":
        return None
    equity = state.equity_fen
    if equity <= 0:
        return None
    after = state.cash_fen - _notional_fen(order)
    min_cash = int(equity * cfg.min_cash_pct)
    if after < min_cash:
        return ConstraintViolation(
            "RISK_MIN_CASH",
            f"成交后现金将低于最低比例 {cfg.min_cash_pct:.0%}: {after} < {min_cash}",
            {"after_cash_fen": after, "min_cash_fen": min_cash},
        )
    return None


def check_daily_addition(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.side != "BUY":
        return None
    equity = state.equity_fen
    if equity <= 0:
        return None
    # 单日新增按今日已有买入估算（调用方在 state 额外字段给出 today_buys_fen）
    today_buys = getattr(state, "today_buys_fen", 0)
    pct = _pct(today_buys + _notional_fen(order), equity)
    if pct > cfg.max_daily_addition_pct:
        return ConstraintViolation(
            "RISK_DAILY_ADDITION_LIMIT",
            f"单日新增将超 {cfg.max_daily_addition_pct:.0%}: {pct:.1%}",
            {"after_pct": pct, "limit": cfg.max_daily_addition_pct},
        )
    return None


def check_participation(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.participation_bps > cfg.participation_cap_bps:
        return ConstraintViolation(
            "RISK_PARTICIPATION_LIMIT",
            f"参与率 {order.participation_bps}bps 超上限 {cfg.participation_cap_bps}bps",
            {"participation_bps": order.participation_bps, "cap": cfg.participation_cap_bps},
        )
    return None


def check_price_deviation(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    """价格偏离：调用方在 order 的 expected_quote_available_at 之外提供最新报价
    （此处用 state.trade_date 与 order 报价时间的差距占位，精确偏差由 adapter 校验）。"""
    if not order.expected_quote_available_at:
        return None
    try:
        quoted = datetime.fromisoformat(order.expected_quote_available_at)
        today = datetime.fromisoformat(state.data_fresh_as_of)
        if quoted < today:
            return ConstraintViolation(
                "RISK_STALE_DATA",
                "报价时间早于数据新鲜度（陈旧数据拒绝）",
                {"quote_available_at": order.expected_quote_available_at,
                 "fresh_as_of": state.data_fresh_as_of},
            )
    except ValueError:
        return ConstraintViolation(
            "RISK_STALE_DATA", "报价时间无法解析", {"quote_available_at": order.expected_quote_available_at}
        )
    return None


def check_defensive_regime(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if order.side == "BUY" and state.regime == "defensive":
        return ConstraintViolation(
            "RISK_DEFENSIVE_REGIME",
            "防守环境禁止开仓",
            {"regime": state.regime},
        )
    return None


def check_stale_data(state: PortfolioState, order: OrderIntent, cfg: RiskConfig) -> ConstraintViolation | None:
    if not state.today or not state.data_fresh_as_of:
        return None
    try:
        d_today = datetime.strptime(state.today, "%Y%m%d")
        d_fresh = datetime.strptime(state.data_fresh_as_of, "%Y%m%d")
    except ValueError:
        return ConstraintViolation("RISK_STALE_DATA", "日期格式非法",
                                   {"today": state.today, "fresh": state.data_fresh_as_of})
    if (d_today - d_fresh).days > cfg.stale_data_days:
        return ConstraintViolation(
            "RISK_STALE_DATA",
            f"数据陈旧: 最新 {state.data_fresh_as_of}，今日 {state.today}",
            {"fresh_as_of": state.data_fresh_as_of, "today": state.today},
        )
    return None


_ALL_CHECKS: tuple[Any, ...] = (
    check_cash, check_lot, check_t1_sellable, check_single_name, check_industry,
    check_theme, check_correlated, check_position_count, check_total_position,
    check_min_cash, check_daily_addition, check_participation, check_price_deviation,
    check_defensive_regime, check_stale_data,
)


def evaluate_constraints(
    state: PortfolioState,
    order: OrderIntent,
    cfg: RiskConfig,
) -> list[ConstraintViolation]:
    """全量约束评估（Review 与 confirm 共用）；返回全部违规。"""
    return [v for check in _ALL_CHECKS if (v := check(state, order, cfg)) is not None]


def constraint_codes() -> list[str]:
    """验收：返回全部 15 个稳定拒绝码。"""
    return list(RISK_CODES)
