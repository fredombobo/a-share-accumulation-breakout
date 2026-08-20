"""Read-only beginner guidance and order review for paper trading."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .cal import is_open, next_open
from .engine import estimate_fill
from .errors import ERR_UNKNOWN_ACCOUNT, DomainError
from .orders import (
    _historical_regime,
    _normalize_trade_date,
    estimate_buy_reserve_fen,
    normalize_ts_code,
    prev_trade_date,
)
from .rules import peek_rule

TUTORIAL_CASH_FEN = 10_000_000


def _yuan(fen: int) -> str:
    return f"{Decimal(fen) / Decimal(100):.2f}"


def _price(value: float) -> str:
    return f"{Decimal(str(value)):.6f}"


def _bar(db_path: Path, ts_code: str, trade_date: str) -> dict[str, float] | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT open,high,low,close,vol,amount FROM daily"
            " WHERE ts_code=? AND trade_date=?",
            (ts_code, trade_date),
        ).fetchone()
    if not row:
        return None
    return {"open": float(row[0]), "high": float(row[1]), "low": float(row[2]),
            "close": float(row[3]), "vol": float(row[4]), "amount": float(row[5])}


def _close_as_of(db_path: Path, ts_code: str, trade_date: str) -> float | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT close FROM daily WHERE ts_code=? AND trade_date<=?"
            " ORDER BY trade_date DESC LIMIT 1",
            (ts_code, trade_date),
        ).fetchone()
    return float(row[0]) if row else None


def _account_state(db_path: Path, decision_date: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        cash_row = conn.execute(
            "SELECT balance_fen FROM pt_cash_flow WHERE account_id=1"
            " ORDER BY flow_id DESC LIMIT 1"
        ).fetchone()
        if cash_row is None:
            raise DomainError(ERR_UNKNOWN_ACCOUNT, "请先创建纸面账户")
        reserved = int(conn.execute(
            "SELECT COALESCE(SUM(reserve_fen),0) FROM pt_order"
            " WHERE account_id=1 AND state IN ('CONFIRMED','QUEUED')"
        ).fetchone()[0])
        market_value = int(round(float(conn.execute(
            "SELECT COALESCE(SUM(l.remaining_qty * COALESCE(("
            "SELECT d.close FROM daily d WHERE d.ts_code=l.ts_code AND d.trade_date<=?"
            " ORDER BY d.trade_date DESC LIMIT 1),0) * 100),0)"
            " FROM pt_position_lot l WHERE l.account_id=1 AND l.remaining_qty>0",
            (decision_date,),
        ).fetchone()[0] or 0)))
    return {"cash_fen": int(cash_row[0]), "reserved_fen": reserved,
            "market_value_fen": market_value}


def _check_account_buy_sequence(
    db_path: Path, ts_code: str, execution_trade_date: str,
) -> None:
    """Mirror the chronology and duplicate checks used by draft creation."""
    with sqlite3.connect(db_path) as conn:
        latest_fill_date = conn.execute(
            "SELECT MAX(substr(quote_revision,instr(quote_revision,':')+1))"
            " FROM pt_fill WHERE instr(quote_revision,':')>0"
        ).fetchone()[0]
        duplicate = conn.execute(
            "SELECT order_id FROM pt_order WHERE account_id=1 AND ts_code=?"
            " AND side='BUY' AND state IN ('CONFIRMED','QUEUED') LIMIT 1",
            (ts_code,),
        ).fetchone()
    if latest_fill_date and execution_trade_date <= str(latest_fill_date):
        raise DomainError(
            "SIMULATION_DATE_NOT_AFTER_LAST_FILL",
            f"模拟日期必须晚于已有成交日期 {latest_fill_date}",
            details={"latest_fill_trade_date": str(latest_fill_date),
                     "execution_trade_date": execution_trade_date},
        )
    if duplicate:
        raise DomainError(
            "DUPLICATE_ACTIVE_ORDER", f"{ts_code} 已有活动买单",
            details={"active_order_id": duplicate[0]},
        )


def review_order(
    db_path: str | Path,
    *,
    scope: str,
    side: str,
    mode: str,
    ts_code: str,
    qty: int,
    execution_trade_date: str,
) -> dict[str, Any]:
    """Review a historical order without writing any database state."""
    db_path = Path(db_path)
    scope = str(scope or "ACCOUNT").upper()
    side = str(side or "BUY").upper()
    mode = str(mode or "MANUAL_HISTORY").upper()
    if scope not in {"ACCOUNT", "TUTORIAL"}:
        raise DomainError("INVALID_REVIEW_SCOPE", "预览范围必须是 ACCOUNT 或 TUTORIAL")
    if side not in {"BUY", "SELL"}:
        raise DomainError("INVALID_ORDER_SIDE", "方向必须是 BUY 或 SELL")
    if mode != "MANUAL_HISTORY":
        raise DomainError("INVALID_REVIEW_MODE", "小白预览仅支持历史开盘演练")

    code = normalize_ts_code(ts_code)
    execution_date = _normalize_trade_date(execution_trade_date)
    if not is_open(db_path, execution_date):
        raise DomainError(
            "NOT_TRADING_DAY", f"{execution_date} 不是交易所开市日",
            details={"execution_trade_date": execution_date,
                     "suggestion": "请选择日历中标记为开市的日期"},
        )
    bar = _bar(db_path, code, execution_date)
    if bar is None or bar["open"] <= 0:
        raise DomainError(
            "NO_QUOTE_FOR_EXECUTION_DATE", f"{code} 在 {execution_date} 没有开盘行情",
            details={"ts_code": code, "execution_trade_date": execution_date},
        )
    decision_date = prev_trade_date(db_path, execution_date)
    if scope == "ACCOUNT" and side == "BUY":
        _check_account_buy_sequence(db_path, code, execution_date)
    if qty <= 0:
        raise DomainError("INVALID_QTY", "数量必须是正整数")

    rule = peek_rule(db_path, code)
    if qty % rule.lot_size != 0:
        raise DomainError(
            "QTY_NOT_MULTIPLE_OF_LOT", f"数量必须是 {rule.lot_size} 股的整数倍",
            details={"lot_size": rule.lot_size},
        )
    fill = estimate_fill(bar, side, qty, rule)
    decision_close = _close_as_of(db_path, code, decision_date)
    if decision_close is None:
        raise DomainError("NO_DECISION_QUOTE", f"{code} 在决策日之前没有行情")
    reserve_fen = estimate_buy_reserve_fen(decision_close, qty, rule) if side == "BUY" else 0

    checks: list[dict[str, Any]] = [
        {"code": "TRADING_DAY", "label": "成交日为开市日", "passed": True,
         "message": f"将使用 {execution_date} 开盘行情"},
        {"code": "LOT_SIZE", "label": "数量符合整手规则", "passed": True,
         "message": f"每手 {rule.lot_size} 股"},
    ]
    try:
        regime = _historical_regime(db_path, decision_date)
        regime_passed = side == "SELL" or bool(regime.allow_new_entries)
        checks.append({"code": "MARKET_REGIME", "label": "市场环境允许开仓",
                       "passed": regime_passed,
                       "message": "允许模拟买入" if regime_passed else "当时处于防守环境"})
    except Exception:  # noqa: BLE001
        checks.append({"code": "MARKET_REGIME", "label": "市场环境数据",
                       "passed": True, "message": "未取得环境结果，正式确认会再次检查"})

    if fill["fill_qty"] == 0:
        checks.append({"code": str(fill["reason"]), "label": "预计可成交",
                       "passed": False, "message": "按当日行情预计无法成交"})
    else:
        checks.append({"code": "LIQUIDITY", "label": "预计成交量足够", "passed": True,
                       "message": f"预计成交 {fill['fill_qty']} 股，参与率上限 5%"})

    cash_fen = TUTORIAL_CASH_FEN
    remaining_cash_fen = cash_fen
    if scope == "ACCOUNT":
        state = _account_state(db_path, decision_date)
        cash_fen = state["cash_fen"] - state["reserved_fen"]
        equity_fen = state["cash_fen"] + state["market_value_fen"]
        remaining_cash_fen = cash_fen - reserve_fen
        risk_checks = [
            ("CASH", reserve_fen <= cash_fen, "可用现金足够"),
            ("CASH_BUFFER", remaining_cash_fen >= int(equity_fen * 0.10),
             "确认后仍保留至少 10% 现金"),
            ("GROSS_EXPOSURE",
             state["market_value_fen"] + int(fill.get("notional_fen", 0))
             <= int(equity_fen * 0.80), "确认后总持仓不超过 80%"),
            ("DAILY_BUY_LIMIT", int(fill.get("notional_fen", 0)) <= int(equity_fen * 0.20),
             "本次买入不超过权益的 20%"),
        ]
        for code_name, passed, message in risk_checks:
            checks.append({"code": code_name, "label": message, "passed": passed,
                           "message": message if passed else f"未通过：{message}"})
    else:
        remaining_cash_fen = cash_fen - reserve_fen
        checks.append({"code": "TUTORIAL_ISOLATED", "label": "隔离演练", "passed": True,
                       "message": "使用固定 10 万元演示资金，不写入你的账户"})

    commission = int(fill.get("commission_fen", 0))
    tax = int(fill.get("tax_fen", 0))
    other = int(fill.get("other_fee_fen", 0))
    notional = int(fill.get("notional_fen", 0))
    cash_change = -(notional + commission + tax + other) if side == "BUY" \
        else notional - commission - tax - other
    return {
        "scope": scope,
        "persisted": False,
        "can_confirm": all(bool(check["passed"]) for check in checks),
        "instrument": {"ts_code": code, "inst_type": rule.inst_type,
                       "lot_size": rule.lot_size},
        "side": side,
        "mode": mode,
        "decision_date": decision_date,
        "execution_trade_date": execution_date,
        "quote": {"open": _price(bar["open"]), "high": _price(bar["high"]),
                  "low": _price(bar["low"]), "close": _price(bar["close"]),
                  "volume": str(int(bar["vol"])),
                  "revision": f"{code}:{execution_date}"},
        "estimate": {
            "requested_qty": qty,
            "estimated_fill_qty": int(fill.get("fill_qty", 0)),
            "max_fill_qty": int(fill.get("max_qty", 0)),
            "fill_price": _price(float(fill.get("fill_price", bar["open"]))),
            "notional_yuan": _yuan(notional),
            "commission_yuan": _yuan(commission),
            "tax_yuan": _yuan(tax),
            "other_fee_yuan": _yuan(other),
            "reserve_yuan": _yuan(reserve_fen),
            "cash_change_yuan": _yuan(cash_change),
            "remaining_cash_yuan": _yuan(remaining_cash_fen),
        },
        "checks": checks,
        "assumptions": {"slippage_bps": rule.slippage_bps,
                        "commission_bps": rule.commission_bps,
                        "sell_tax_bps": rule.sell_tax_bps,
                        "participation_limit_pct": "5"},
    }


def build_guide(db_path: str | Path) -> dict[str, Any]:
    """Derive exactly one next action from persisted account state."""
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as conn:
        latest_market = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        account = conn.execute("SELECT account_id FROM pt_account LIMIT 1").fetchone()
        latest_fill = conn.execute(
            "SELECT MAX(substr(quote_revision,instr(quote_revision,':')+1))"
            " FROM pt_fill WHERE instr(quote_revision,':')>0"
        ).fetchone()[0]
        unresolved = int(conn.execute(
            "SELECT COUNT(*) FROM pt_reconciliation"
            " WHERE status IN ('OPEN','ESCALATED') AND result!='OK'"
        ).fetchone()[0])
        pending = conn.execute(
            "SELECT order_id,source,ts_code,side,qty,state,eligible_trade_date"
            " FROM pt_order WHERE state IN ('DRAFT','CONFIRMED','QUEUED')"
            " ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    if account is None:
        next_action = "CREATE_ACCOUNT"
    elif unresolved:
        next_action = "RESOLVE_RECONCILIATION"
    elif pending and pending[5] == "DRAFT":
        next_action = "REVIEW_DRAFT"
    elif pending:
        next_action = "RUN_SETTLEMENT"
    else:
        next_action = "START_SIMULATION"

    earliest = None
    if latest_fill:
        next_day = (datetime.strptime(str(latest_fill), "%Y%m%d").date()
                    + timedelta(days=1)).strftime("%Y%m%d")
        earliest = next_open(db_path, next_day)
    blocker_codes = ["RECONCILIATION_OPEN"] if unresolved else []
    if (
        next_action == "START_SIMULATION"
        and earliest
        and latest_market
        and str(earliest) > str(latest_market)
    ):
        next_action = "SYNC_DATA"
        blocker_codes.append("LEDGER_AHEAD_OF_MARKET")
    pending_payload = None if pending is None else {
        "order_id": pending[0], "source": pending[1], "ts_code": pending[2],
        "side": pending[3], "qty": pending[4], "state": pending[5],
        "eligible_trade_date": pending[6],
    }
    return {
        "next_action": next_action,
        "blocker_codes": blocker_codes,
        "pending_order": pending_payload,
        "earliest_simulation_date": earliest,
        "latest_market_date": str(latest_market) if latest_market else None,
        "unresolved_reconciliation_count": unresolved,
    }


def trading_calendar(db_path: str | Path, *, start: str, end: str) -> dict[str, Any]:
    """Return local exchange open dates and the current ledger date bounds."""
    db_path = Path(db_path)
    start = _normalize_trade_date(start)
    end = _normalize_trade_date(end)
    if start > end:
        raise DomainError("INVALID_DATE_RANGE", "开始日期不能晚于结束日期")
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT cal_date FROM trade_cal WHERE cal_date BETWEEN ? AND ? AND is_open=1"
            " ORDER BY cal_date", (start, end),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM daily WHERE trade_date BETWEEN ? AND ?"
                " ORDER BY trade_date", (start, end),
            ).fetchall()
    guide = build_guide(db_path)
    return {"open_dates": [str(row[0]) for row in rows],
            "earliest_simulation_date": guide["earliest_simulation_date"],
            "latest_market_date": guide["latest_market_date"]}
