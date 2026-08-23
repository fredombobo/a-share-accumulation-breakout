"""纸面风险适配（P5.1/P5.2）：pt DB → 约束评估 + 组合风险报告。

- Review 与 confirm 共用 `evaluate_order_risk`（observe 模式：返回违规，是否阻断由
  V2_RISK_ENFORCEMENT_ENABLED 决定——默认 false，先 observe 后 enforce）。
- 硬约束（现金/份额/不做空/T+1）永不可关闭，由 legacy confirm 强制。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ab_screener.domain.risk.constraints import evaluate_constraints
from ab_screener.domain.risk.models import (
    OrderIntent,
    PortfolioState,
    Position,
    RiskConfig,
)

RISK_ENFORCE_DEFAULT = False  # observe 模式（先观察后强制；配置旗标覆盖）


def _enforcement_enabled() -> bool:
    """读取 resolved config 的 V2_RISK_ENFORCEMENT_ENABLED（默认 false）。"""
    try:
        from ab_screener.application.platform_config import load_resolved_config

        flags = load_resolved_config().get("flags") or {}
        return bool(flags.get("V2_RISK_ENFORCEMENT_ENABLED", RISK_ENFORCE_DEFAULT))
    except Exception:  # noqa: BLE001
        return RISK_ENFORCE_DEFAULT


def _risk_config() -> RiskConfig:
    """从 configs/risk/robust_personal_v2.yaml 冻结阈值加载（失败 → 保守默认）。"""
    try:
        import yaml

        path = Path(__file__).resolve().parents[2] / "configs" / "risk" / "robust_personal_v2.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        c = data["constraints"]
        return RiskConfig(
            max_single_name_pct=c["max_single_name_pct"],
            max_industry_pct=c["max_industry_pct"],
            max_theme_pct=c["max_theme_pct"],
            max_corr_group_pct=c["max_corr_group_pct"],
            max_position_count=c["max_position_count"],
            max_total_position_pct=c["max_total_position_pct"],
            min_cash_pct=c["min_cash_pct"],
            max_daily_addition_pct=c["max_daily_addition_pct"],
            max_price_deviation_pct=c["max_price_deviation_pct"],
            stale_data_days=c["stale_data_days"],
            lot_size=c["lot_size"],
            participation_cap_bps=c["participation_cap_bps"],
        )
    except Exception:  # noqa: BLE001
        return RiskConfig()


def build_portfolio_state(db_path: str | Path, *, today: str) -> PortfolioState:
    """从 pt DB 组装组合状态（含持仓/可卖/行业由 adapter 简化填空）。"""
    db_path = Path(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        cash_row = conn.execute(
            "SELECT balance_fen FROM pt_cash_flow WHERE account_id=1"
            " ORDER BY flow_id DESC LIMIT 1"
        ).fetchone()
        lots = conn.execute(
            "SELECT ts_code, SUM(remaining_qty), SUM(CASE WHEN sellable_date<=? THEN remaining_qty ELSE 0 END)"
            " FROM pt_position_lot WHERE account_id=1 AND remaining_qty>0 GROUP BY ts_code",
            (today,),
        ).fetchall()
        fresh_row = conn.execute(
            "SELECT MAX(trade_date) FROM daily"
        ).fetchone()
    cash_fen = int(cash_row[0]) if cash_row else 0
    positions = [
        Position(ts_code=r[0], qty=int(r[1]), sellable_qty=int(r[2] or 0))
        for r in lots
    ]
    # 最新收盘价（估值用）
    with sqlite3.connect(str(db_path)) as conn:
        for i, p in enumerate(positions):
            row = conn.execute(
                "SELECT close FROM daily WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
                (p.ts_code,),
            ).fetchone()
            if row:
                positions[i] = Position(
                    ts_code=p.ts_code, qty=p.qty, sellable_qty=p.sellable_qty,
                    latest_close_micro=int(round(float(row[0]) * 1_000_000)),
                )
    positions_t = tuple(positions)
    equity = cash_fen + sum(
        p.latest_close_micro * p.qty // 10_000 for p in positions
    )
    return PortfolioState(
        cash_fen=cash_fen, equity_fen=equity, positions=positions_t,
        today=today, trade_date=today, regime="neutral",
        data_fresh_as_of=str(fresh_row[0] or ""),
    )


def evaluate_order_risk(
    db_path: str | Path,
    *,
    ts_code: str,
    side: str,
    qty: int,
    price_micro: int,
    today: str,
    participation_bps: int = 500,
) -> dict[str, Any]:
    """订单风险评估（Review 与 confirm 共用；enforce 模式 fail-closed）。"""
    enforce = _enforcement_enabled()
    try:
        state = build_portfolio_state(db_path, today=today)
        order = OrderIntent(
            ts_code=ts_code, side=side, qty=qty, price_micro=price_micro,
            participation_bps=participation_bps,
        )
        violations = evaluate_constraints(state, order, _risk_config())
    except Exception as exc:
        if enforce:
            # fail-closed：enforce 模式下风控不可用 → 拒绝
            return {
                "ts_code": ts_code, "side": side, "today": today,
                "violations": [{"code": "RISK_UNAVAILABLE", "message": str(exc)[:200]}],
                "blocked": True,
                "mode": "enforce",
            }
        raise
    return {
        "ts_code": ts_code, "side": side, "today": today,
        "violations": [v.to_dict() for v in violations],
        "blocked": bool(violations) and enforce,
        "mode": "enforce" if enforce else "observe",
    }
