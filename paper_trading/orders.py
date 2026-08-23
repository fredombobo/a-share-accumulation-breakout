"""订单领域：草稿生成、确认风控、取消、状态机。

状态机（固定）：
  DRAFT → CONFIRMED → QUEUED → FILLED | PARTIALLY_FILLED_EXPIRED | EXPIRED | REJECTED
  DRAFT → CANCELLED
  CONFIRMED/QUEUED → CANCELLED（前提：尚未进入当日撮合事务）

规则：
- 买入草稿只能来自当日 A 池且 tradeable=true 的信号快照
- 防守环境不生成新买入草稿；撮合前再次检查，转为禁止开仓时买单拒绝
- 卖出草稿只能来自现有可卖持仓，不允许卖空
- 确认时执行现金/持仓/T+1/仓位/市场环境/行情时点/成交量/交易规则检查
- 买单确认时按可能成交上限预留现金；卖单确认时预留可卖份额
- 未配置标的规则、行情过期、现金不足、重复活动订单、数量不足一手 → 明确拒单
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .cal import is_open, next_open
from .db import tx
from .errors import (
    ERR_INSUFFICIENT_CASH,
    ERR_INSUFFICIENT_SELLABLE,
    ERR_INVALID_STATE,
    ERR_STALE_QUOTE,
    ERR_UNKNOWN_ACCOUNT,
    DomainError,
)
from .rules import get_rule

_TZ = ZoneInfo("Asia/Shanghai")

ORDER_STATES = ("DRAFT", "CONFIRMED", "QUEUED", "FILLED",
                "PARTIALLY_FILLED_EXPIRED", "EXPIRED", "REJECTED", "CANCELLED")
# 允许从 DRAFT/CONFIRMED/QUEUED 取消
_CANCELABLE = {"DRAFT", "CONFIRMED", "QUEUED"}
# 活动订单（占用预留）
_ACTIVE = {"CONFIRMED", "QUEUED"}


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _strict_next_open(db_path: Path, d: str) -> str:
    current = datetime.strptime(d, "%Y%m%d").date() + timedelta(days=1)
    return next_open(db_path, current.strftime("%Y%m%d"))


def _confirmation_time(today: str) -> str:
    """测试注入日期时使用收盘后时点；生产环境使用真实含时区时间。"""
    real_today = datetime.now(_TZ).strftime("%Y%m%d")
    if today == real_today:
        return _now()
    return f"{today[:4]}-{today[4:6]}-{today[6:8]}T15:31:00+08:00"


def _normalize_time(value: str) -> datetime:
    normalized = value.strip().replace(" ", "T", 1)
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TZ)
    return parsed


def _get_account_cash(db_path: Path) -> int:
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT balance_fen FROM pt_cash_flow WHERE account_id=1"
            " ORDER BY flow_id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise DomainError(ERR_UNKNOWN_ACCOUNT, "纸面账户不存在")
    return int(row[0])


def _latest_quote(db_path: Path, ts_code: str) -> tuple[str, float, float] | None:
    """最新行情 (trade_date, close, vol)。无数据返回 None。"""
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT trade_date, close, vol FROM daily WHERE ts_code=?"
            " ORDER BY trade_date DESC LIMIT 1", (ts_code,)
        ).fetchone()
    return (str(row[0]), float(row[1]), float(row[2])) if row else None


def normalize_ts_code(ts_code: str) -> str:
    """Normalize the six-digit code accepted by the personal paper UI."""
    value = str(ts_code or "").strip().upper()
    if len(value) == 6 and value.isdigit():
        if value.startswith(("4", "8")):
            return f"{value}.BJ"
        if value.startswith(("5", "6", "9")):
            return f"{value}.SH"
        return f"{value}.SZ"
    if len(value) == 9 and value[:6].isdigit() and value[6:] in {".SH", ".SZ", ".BJ"}:
        return value
    raise DomainError(
        "INVALID_TS_CODE",
        f"无效证券代码：{ts_code}",
        details={"expected": "六位代码或 000001.SZ / 688105.SH"},
    )


def _normalize_trade_date(value: str) -> str:
    normalized = str(value or "").strip().replace("-", "")
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise DomainError(
            "INVALID_TRADE_DATE",
            f"无效交易日期：{value}",
            details={"expected": "YYYYMMDD 或 YYYY-MM-DD"},
        ) from exc
    return normalized


def _quote_as_of(
    db_path: Path, ts_code: str, trade_date: str,
) -> tuple[str, float, float] | None:
    """Latest quote visible no later than ``trade_date``."""
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT trade_date, close, vol FROM daily WHERE ts_code=? AND trade_date<=?"
            " ORDER BY trade_date DESC LIMIT 1",
            (ts_code, trade_date),
        ).fetchone()
    return (str(row[0]), float(row[1]), float(row[2])) if row else None


def _has_execution_bar(db_path: Path, ts_code: str, trade_date: str) -> bool:
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT 1 FROM daily WHERE ts_code=? AND trade_date=? AND open>0 LIMIT 1",
            (ts_code, trade_date),
        ).fetchone()
    return row is not None


def _historical_regime(db_path: Path, decision_date: str):
    """Evaluate the market regime with index data available by the decision date."""
    import pandas as pd

    from market_regime import detect_regime_from_index_df

    with tx(db_path, immediate=False) as conn:
        rows = conn.execute(
            "SELECT trade_date, close FROM daily WHERE ts_code='000300.SH'"
            " AND trade_date<=? ORDER BY trade_date",
            (decision_date,),
        ).fetchall()
    return detect_regime_from_index_df(
        pd.DataFrame(rows, columns=["trade_date", "close"]),
        index_code="000300.SH",
    )


def _tradeable_signal(db_path: Path, ts_code: str, trade_date: str) -> dict[str, Any] | None:
    """当日 A 池可交易信号（pool='A'）。无 → None。"""
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT trade_date, ts_code, pool, total_score, suggested_pos_pct,"
            " input_hash, available_at FROM pt_signal_snapshot"
            " WHERE trade_date=? AND ts_code=? AND pool='A' AND tradeable=1",
            (trade_date, ts_code),
        ).fetchone()
    if not row:
        return None
    return {
        "trade_date": row[0], "ts_code": row[1], "pool": row[2],
        "total_score": row[3], "suggested_pos_pct": row[4],
        "input_hash": row[5], "available_at": row[6],
    }


# ── 数量计算 ──

def _calc_qty(equity_fen: int, price: float, pos_pct: float | None, lot_size: int) -> int:
    """按账户权益 × min(建议仓位, 10%) 计算买入数量，向下取整到整手。"""
    pct = min(float(pos_pct or 5.0), 10.0) / 100.0
    budget_fen = int(equity_fen * pct)
    qty_float = budget_fen / (price * 100.0) if price > 0 else 0.0
    qty = int(qty_float // lot_size) * lot_size
    return max(0, qty)


def estimate_buy_reserve_fen(price: float, qty: int, rule) -> int:
    """Use the formal confirmation formula to estimate the cash reservation."""
    est_price = price * (1 + rule.slippage_bps / 10_000.0)
    fee_rate = (rule.commission_bps + rule.other_fee_bps) / 10_000.0
    est_cost = est_price * qty * 100.0
    return int(est_cost * (1 + fee_rate)) + rule.min_commission_fen


# ── 草稿生成 ──

def create_buy_draft(
    db_path: str | Path,
    *,
    ts_code: str,
    trade_date: str,
    suggested_pos_pct: float | None = None,
    total_score: float | None = None,
    input_hash: str = "",
    qty: int | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """从 A 池信号创建买入草稿。防守环境 / 非 A 池 / 已有活动买单 → 拒。

    qty 缺省时按权益×建议仓位计算；显式 qty 不得超过建议值或风控上限。
    today：测试注入固定交易日（默认真实日期）。
    """
    db_path = Path(db_path)
    ts_code = normalize_ts_code(ts_code)
    trade_date = _normalize_trade_date(trade_date)
    # 信号检查：必须是当日已按 PIT 环境固化为 tradeable 的 A 池快照。
    # 最新环境在确认和撮合阶段复核，避免用“当前环境”反向改写历史信号。
    sig = _tradeable_signal(db_path, ts_code, trade_date)
    if sig is None:
        raise DomainError("SIGNAL_NOT_TRADEABLE",
                          f"{ts_code} 当日无 A 池可交易信号（{trade_date}）")

    # 重复活动买单检查
    with tx(db_path, immediate=False) as conn:
        dup = conn.execute(
            "SELECT 1 FROM pt_order WHERE ts_code=? AND side='BUY'"
            " AND state IN ('CONFIRMED','QUEUED') AND account_id=1 LIMIT 1", (ts_code,)
        ).fetchone()
    if dup:
        raise DomainError("DUPLICATE_ACTIVE_ORDER",
                          f"{ts_code} 已有活动买单，禁止重复买入")

    rule = get_rule(db_path, ts_code)
    quote = _quote_as_of(db_path, ts_code, trade_date)
    if quote is None:
        raise DomainError("NO_QUOTE", f"{ts_code} 无行情数据")
    price = quote[1]
    equity_fen = _get_account_cash(db_path)
    # 权益 = 现金 + 持仓市值（简化：用现金，后续日结完善）
    auto_qty = _calc_qty(equity_fen, price, suggested_pos_pct, rule.lot_size)
    if qty is None:
        qty = auto_qty
    else:
        # 用户可降低，不得提高
        cap = max(auto_qty, rule.lot_size)
        if qty > cap:
            raise DomainError("QTY_EXCEEDS_CAP",
                              f"数量 {qty} 超过建议上限 {cap}",
                              details={"cap": cap})
    if qty < rule.lot_size:
        raise DomainError("QTY_BELOW_LOT", f"数量低于一手（{rule.lot_size}）",
                          details={"lot_size": rule.lot_size})

    order_id = _new_id("ORD")
    now = _now()
    with tx(db_path, immediate=True) as conn:
        conn.execute(
            "INSERT INTO pt_order (order_id, idempotency_key, account_id, source,"
            " ts_code, side, qty, state, reserve_fen, reserved_qty, signal_trade_date,"
            " created_at, updated_at)"
            " VALUES (?,?,1,'SIGNAL',?,'BUY',?,'DRAFT',0,0,?,?,?)",
            (order_id, f"draft-{uuid.uuid4().hex[:12]}", ts_code, qty,
             trade_date, now, now),
        )
        conn.execute(
            "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
            " before_json, after_json, occurred_at)"
            " VALUES ('system','ORDER_DRAFT_CREATE','order',?,NULL,?,?)",
            (order_id, f'{{"side":"BUY","qty":{qty},"signal_input_hash":"{input_hash}"}}', now),
        )
    return get_order(db_path, order_id)


def create_historical_buy_draft(
    db_path: str | Path,
    *,
    ts_code: str,
    execution_trade_date: str,
    qty: int,
) -> dict[str, Any]:
    """Create an explicit manual paper order for a selected historical open.

    This path is intentionally separate from signal orders: it never claims that
    the instrument belonged to pool A.  The decision timestamp is frozen after
    the previous open day's close, and matching is restricted to the selected
    next exchange trading day.
    """
    db_path = Path(db_path)
    ts_code = normalize_ts_code(ts_code)
    execution_trade_date = _normalize_trade_date(execution_trade_date)
    if not is_open(db_path, execution_trade_date):
        raise DomainError(
            "NOT_TRADING_DAY",
            f"{execution_trade_date} 不是交易所开市日",
            details={"execution_trade_date": execution_trade_date},
        )
    if not _has_execution_bar(db_path, ts_code, execution_trade_date):
        raise DomainError(
            "NO_QUOTE_FOR_EXECUTION_DATE",
            f"{ts_code} 在 {execution_trade_date} 没有可用开盘行情",
            details={"ts_code": ts_code, "execution_trade_date": execution_trade_date},
        )

    decision_date = prev_trade_date(db_path, execution_trade_date)
    if _strict_next_open(db_path, decision_date) != execution_trade_date:
        raise DomainError(
            "INVALID_HISTORICAL_EXECUTION_DATE",
            "历史手工订单只能在决策日后的下一开市日开盘撮合",
            details={"decision_date": decision_date,
                     "execution_trade_date": execution_trade_date},
        )

    with tx(db_path, immediate=False) as conn:
        latest_fill_date = conn.execute(
            "SELECT MAX(substr(quote_revision,instr(quote_revision,':')+1))"
            " FROM pt_fill WHERE instr(quote_revision,':')>0",
        ).fetchone()[0]
        duplicate = conn.execute(
            "SELECT order_id FROM pt_order WHERE account_id=1 AND ts_code=? AND side='BUY'"
            " AND state IN ('CONFIRMED','QUEUED') LIMIT 1",
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
            "DUPLICATE_ACTIVE_ORDER",
            f"{ts_code} 已有活动买单",
            details={"active_order_id": duplicate[0]},
        )

    rule = get_rule(db_path, ts_code)
    if qty <= 0:
        raise DomainError("INVALID_QTY", "买入数量必须为正整数")
    if qty % rule.lot_size != 0:
        raise DomainError(
            "QTY_NOT_MULTIPLE_OF_LOT",
            f"数量必须是整手（{rule.lot_size}）的整数倍",
            details={"lot_size": rule.lot_size},
        )

    order_id = _new_id("ORD")
    now = _now()
    with tx(db_path, immediate=True) as conn:
        conn.execute(
            "INSERT INTO pt_order (order_id, idempotency_key, account_id, source,"
            " ts_code, side, qty, state, reserve_fen, reserved_qty, signal_trade_date,"
            " eligible_trade_date, created_at, updated_at)"
            " VALUES (?,?,1,'MANUAL_HISTORY',?,'BUY',?,'DRAFT',0,0,?,?,?,?)",
            (order_id, f"draft-{uuid.uuid4().hex[:12]}", ts_code, qty,
             decision_date, execution_trade_date, now, now),
        )
        conn.execute(
            "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
            " before_json, after_json, occurred_at)"
            " VALUES ('user','HISTORICAL_ORDER_DRAFT_CREATE','order',?,NULL,?,?)",
            (order_id,
             f'{{"side":"BUY","qty":{qty},"decision_date":"{decision_date}",'
             f'"execution_trade_date":"{execution_trade_date}"}}', now),
        )
    return get_order(db_path, order_id)


def create_sell_draft(
    db_path: str | Path,
    *,
    ts_code: str,
    qty: int,
    today: str | None = None,
) -> dict[str, Any]:
    """从现有可卖持仓创建卖出草稿（不允许卖空/超卖）。"""
    db_path = Path(db_path)
    ts_code = normalize_ts_code(ts_code)
    sellable = sellable_qty(db_path, ts_code, today=today)
    if sellable <= 0:
        raise DomainError(ERR_INSUFFICIENT_SELLABLE,
                          f"{ts_code} 无可卖份额（T+1 或未持仓）",
                          details={"sellable": sellable})
    if qty <= 0:
        raise DomainError("INVALID_QTY", "卖出数量必须为正")
    if qty > sellable:
        raise DomainError(ERR_INSUFFICIENT_SELLABLE,
                          f"可卖份额不足：请求 {qty}，可卖 {sellable}",
                          details={"requested": qty, "sellable": sellable})
    rule = get_rule(db_path, ts_code)
    if qty % rule.lot_size != 0:
        raise DomainError("QTY_NOT_MULTIPLE_OF_LOT",
                          f"数量必须是整手（{rule.lot_size}）的整数倍",
                          details={"lot_size": rule.lot_size})

    order_id = _new_id("ORD")
    now = _now()
    with tx(db_path, immediate=True) as conn:
        conn.execute(
            "INSERT INTO pt_order (order_id, idempotency_key, account_id, source,"
            " ts_code, side, qty, state, reserve_fen, reserved_qty, created_at, updated_at)"
            " VALUES (?,?,1,'POSITION',?,'SELL',?,'DRAFT',0,0,?,?)",
            (order_id, f"draft-{uuid.uuid4().hex[:12]}", ts_code, qty, now, now),
        )
    return get_order(db_path, order_id)


def sellable_qty(db_path: str | Path, ts_code: str, today: str | None = None) -> int:
    """可卖份额 = 批次剩余份额（sellable_date <= today）。"""
    db_path = Path(db_path)
    today = today or datetime.now(_TZ).strftime("%Y%m%d")
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(remaining_qty),0) FROM pt_position_lot"
            " WHERE ts_code=? AND account_id=1 AND sellable_date <= ?",
            (ts_code, today),
        ).fetchone()
    return int(row[0])


# ── 确认 ──

def confirm_order(
    db_path: str | Path,
    order_id: str,
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """确认订单：预交易检查 + 预留资产（BUY 预留现金 / SELL 预留份额）。

    幂等：已 CONFIRMED/QUEUED 返回原结果；终态订单拒绝。
    today：测试注入固定交易日（默认真实系统日期，Asia/Shanghai）。
    """
    db_path = Path(db_path)
    order = get_order(db_path, order_id)
    state = order["state"]
    if state in ("CONFIRMED", "QUEUED"):
        return order  # 幂等：已确认返回原结果
    if state not in ("DRAFT",):
        raise DomainError(ERR_INVALID_STATE,
                          f"仅 DRAFT 可确认，当前 {state}",
                          details={"order_id": order_id, "state": state})

    checks: list[dict[str, Any]] = []
    ts_code = order["ts_code"]
    side = order["side"]
    qty = order["qty"]
    historical_manual = order.get("source") == "MANUAL_HISTORY"
    historical_execution_date = str(order.get("eligible_trade_date") or "")
    if historical_manual:
        today = str(order.get("signal_trade_date") or "")
        if not today or not historical_execution_date:
            raise DomainError(
                "INVALID_HISTORICAL_ORDER",
                "历史手工订单缺少决策日或模拟成交日",
                details={"order_id": order_id},
            )

    # 1) 市场环境（撮合前再次检查）
    regime = "neutral"
    try:
        if historical_manual:
            reg = _historical_regime(db_path, str(today))
        else:
            from local_store import LocalStore
            from market_regime import detect_regime

            reg = detect_regime(store=LocalStore(db_path=db_path), allow_network=False)
        regime = reg.regime
        if side == "BUY" and not reg.allow_new_entries:
            _reject(order_id, "MARKET_DEFENSE", f"防守环境禁止开仓（{regime}）", db_path)
            raise DomainError("MARKET_DEFENSE", "防守环境禁止开仓",
                              details={"regime": regime})
        checks.append({"name": "市场环境", "pass": True, "regime": regime})
    except DomainError:
        raise
    except Exception:  # noqa: BLE001
        pass

    # 2) 行情时点（过期行情拒单）
    today = today or datetime.now(_TZ).strftime("%Y%m%d")
    quote = _quote_as_of(db_path, ts_code, today)
    if quote is None:
        _reject(order_id, "NO_QUOTE", "无行情数据", db_path)
        raise DomainError("NO_QUOTE", f"{ts_code} 无行情")
    q_date, price, _vol = quote
    confirmed_at = _confirmation_time(today)
    eligible_trade_date = (
        historical_execution_date if historical_manual else _strict_next_open(db_path, today)
    )
    if historical_manual and _strict_next_open(db_path, today) != eligible_trade_date:
        _reject(order_id, "INVALID_HISTORICAL_ORDER", "模拟成交日不是决策日后的下一开市日", db_path)
        raise DomainError(
            "INVALID_HISTORICAL_ORDER",
            "模拟成交日不是决策日后的下一开市日",
            details={"decision_date": today, "execution_trade_date": eligible_trade_date},
        )
    stale = False
    try:
        # 行情必须不早于「最近已收盘交易日」（今天尚未收盘，取今天的前一交易日）
        if q_date < prev_trade_date(db_path, today):
            stale = True
    except Exception:  # noqa: BLE001
        stale = False
    if stale:
        _reject(order_id, ERR_STALE_QUOTE, f"行情过期（最新 {q_date}）", db_path)
        raise DomainError(ERR_STALE_QUOTE, "行情过期，禁止成交",
                          details={"latest_quote": q_date, "today": today})
    checks.append({"name": "行情时点", "pass": True, "as_of": q_date})

    if side == "BUY" and not historical_manual:
        sig = _tradeable_signal(db_path, ts_code, str(order.get("signal_trade_date") or today))
        if sig is None:
            _reject(order_id, "SIGNAL_NOT_TRADEABLE", "A 池信号不存在", db_path)
            raise DomainError("SIGNAL_NOT_TRADEABLE", "A 池信号不存在")
        if str(sig["trade_date"]) != today:
            _reject(order_id, "SIGNAL_EXPIRED", "信号仅在交易日当日可确认", db_path)
            raise DomainError("SIGNAL_EXPIRED", "信号已过期",
                              details={"signal_trade_date": sig["trade_date"], "today": today})
        try:
            if _normalize_time(str(sig["available_at"])) > _normalize_time(confirmed_at):
                _reject(order_id, "SIGNAL_NOT_AVAILABLE", "信号在确认时点尚不可用", db_path)
                raise DomainError("SIGNAL_NOT_AVAILABLE", "信号在确认时点尚不可用")
        except ValueError as exc:
            _reject(order_id, "INVALID_SIGNAL_TIME", "信号时点格式无效", db_path)
            raise DomainError("INVALID_SIGNAL_TIME", "信号时点格式无效") from exc
        checks.append({"name": "信号时点", "pass": True, "available_at": sig["available_at"]})
    elif side == "BUY":
        checks.append({
            "name": "历史手工演练",
            "pass": True,
            "decision_date": today,
            "execution_trade_date": eligible_trade_date,
            "note": "不声明 A 池资格，仅用于纸面历史演练",
        })

    # 3) 交易规则
    rule = get_rule(db_path, ts_code)
    checks.append({"name": "交易规则", "pass": True, "inst_type": rule.inst_type})

    # 4) 数量检查（BUY：现金足额；SELL：可卖份额）
    reserve_fen = 0
    if side == "BUY":
        reserve_fen = estimate_buy_reserve_fen(price, qty, rule)
    else:
        reserve_fen = 0

    # 4.5) 统一风控（与 review 共享同一入口；确认不信任前端提交的风控结果）
    try:
        from paper_trading.risk_adapter import evaluate_order_risk

        risk = evaluate_order_risk(
            db_path,
            ts_code=ts_code,
            side=side,
            qty=qty,
            price_micro=int(round(price * 1_000_000)),
            today=today,
        )
        if risk.get("blocked"):
            detail = risk.get("violations") or []
            _reject(order_id, "RISK_BLOCKED", f"统一风控拒绝: {detail}", db_path)
            raise DomainError("RISK_BLOCKED", "统一风控拒绝", details=detail)
        checks.append({
            "name": "统一风控", "pass": True, "mode": risk.get("mode"),
            "degraded": bool(risk.get("degraded")),
        })
    except DomainError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 统一入口承诺不抛出；此处兜底（禁止裸吞）：enforce → fail-closed 拒单
        from paper_trading.risk_adapter import _enforcement_enabled

        if _enforcement_enabled():
            _reject(order_id, "RISK_BLOCKED", f"统一风控不可用（enforce fail-closed）: {exc}", db_path)
            raise DomainError("RISK_BLOCKED", "统一风控不可用（enforce fail-closed）",
                              details={"error": str(exc)[:200]})
        checks.append({"name": "统一风控", "pass": True, "mode": "observe", "degraded": True,
                       "note": f"风控入口异常（observe 降级）: {str(exc)[:120]}"})

    # 5) 预留 + 状态流转（BEGIN IMMEDIATE 内）
    now = _now()
    pending_error: DomainError | None = None
    reserved_qty = qty if side == "SELL" else 0
    with tx(db_path, immediate=True) as conn:
        current = conn.execute(
            "SELECT state FROM pt_order WHERE order_id=?", (order_id,)
        ).fetchone()
        if not current or current[0] != "DRAFT":
            pending_error = DomainError(ERR_INVALID_STATE, "订单状态已变化，请刷新后重试")
        elif side == "BUY":
            est_price = price * (1 + rule.slippage_bps / 10_000.0)
            cash_row = conn.execute(
                "SELECT balance_fen FROM pt_cash_flow WHERE account_id=1 "
                "ORDER BY flow_id DESC LIMIT 1"
            ).fetchone()
            cash = int(cash_row[0]) if cash_row else 0
            other_reserved = int(conn.execute(
                "SELECT COALESCE(SUM(reserve_fen),0) FROM pt_order "
                "WHERE account_id=1 AND state IN ('CONFIRMED','QUEUED') AND order_id<>?",
                (order_id,),
            ).fetchone()[0])
            available_cash = cash - other_reserved
            duplicate = conn.execute(
                "SELECT order_id FROM pt_order WHERE account_id=1 AND ts_code=? "
                "AND side='BUY' AND state IN ('CONFIRMED','QUEUED') AND order_id<>? LIMIT 1",
                (ts_code, order_id),
            ).fetchone()
            if historical_manual:
                market_value_row = conn.execute(
                    "SELECT COALESCE(SUM(l.remaining_qty * COALESCE(("
                    "SELECT d.close FROM daily d WHERE d.ts_code=l.ts_code "
                    "AND d.trade_date<=? ORDER BY d.trade_date DESC LIMIT 1),0) * 100),0) "
                    "FROM pt_position_lot l WHERE l.account_id=1 AND l.remaining_qty>0",
                    (today,),
                ).fetchone()
            else:
                market_value_row = conn.execute(
                    "SELECT COALESCE(SUM(l.remaining_qty * COALESCE(("
                    "SELECT d.close FROM daily d WHERE d.ts_code=l.ts_code "
                    "ORDER BY d.trade_date DESC LIMIT 1),0) * 100),0) "
                    "FROM pt_position_lot l WHERE l.account_id=1 AND l.remaining_qty>0"
                ).fetchone()
            market_value = int(round(float(market_value_row[0] or 0)))
            equity = cash + market_value
            order_notional = int(round(est_price * qty * 100.0))
            daily_buy_notional = int(round(float(conn.execute(
                "SELECT COALESCE(SUM(o.qty * COALESCE((SELECT d.close FROM daily d "
                "WHERE d.ts_code=o.ts_code ORDER BY d.trade_date DESC LIMIT 1),0) * 100),0) "
                "FROM pt_order o "
                "WHERE account_id=1 AND side='BUY' AND state IN ('CONFIRMED','QUEUED') "
                "AND eligible_trade_date=? AND order_id<>?",
                (eligible_trade_date, order_id),
            ).fetchone()[0] or 0)))
            if duplicate:
                pending_error = DomainError(
                    "DUPLICATE_ACTIVE_ORDER", f"{ts_code} 已有活动买单",
                    details={"active_order_id": duplicate[0]},
                )
            elif reserve_fen > available_cash:
                pending_error = DomainError(
                    ERR_INSUFFICIENT_CASH, "现金不足",
                    details={"reserve_fen": reserve_fen, "cash_fen": cash,
                             "available_cash_fen": available_cash},
                )
            elif market_value + order_notional > int(equity * 0.80):
                pending_error = DomainError(
                    "GROSS_EXPOSURE_LIMIT_EXCEEDED", "确认后总持仓将超过账户权益的 80%",
                    details={"equity_fen": equity, "market_value_fen": market_value,
                             "order_notional_fen": order_notional, "limit_pct": "80"},
                )
            elif available_cash - reserve_fen < int(equity * 0.10):
                pending_error = DomainError(
                    "CASH_BUFFER_LIMIT_EXCEEDED", "确认后现金将低于账户权益的 10%",
                    details={"equity_fen": equity,
                             "remaining_cash_fen": available_cash - reserve_fen,
                             "limit_pct": "10"},
                )
            elif daily_buy_notional + order_notional > int(equity * 0.20):
                pending_error = DomainError(
                    "DAILY_BUY_LIMIT_EXCEEDED", "单日新增买入预留将超过账户权益的 20%",
                    details={"equity_fen": equity,
                             "already_buy_notional_fen": daily_buy_notional,
                             "new_buy_notional_fen": order_notional, "limit_pct": "20"},
                )
            else:
                checks.extend([
                    {"name": "现金充足", "pass": True, "reserve_fen": reserve_fen},
                    {"name": "总仓位上限", "pass": True, "limit_pct": "80"},
                    {"name": "现金缓冲", "pass": True, "limit_pct": "10"},
                    {"name": "单日买入上限", "pass": True, "limit_pct": "20"},
                ])
        else:
            sellable = int(conn.execute(
                "SELECT COALESCE(SUM(remaining_qty),0) FROM pt_position_lot "
                "WHERE ts_code=? AND account_id=1 AND sellable_date<=?",
                (ts_code, today),
            ).fetchone()[0])
            already_reserved = int(conn.execute(
                "SELECT COALESCE(SUM(reserved_qty),0) FROM pt_order "
                "WHERE account_id=1 AND ts_code=? AND side='SELL' "
                "AND state IN ('CONFIRMED','QUEUED') AND order_id<>?",
                (ts_code, order_id),
            ).fetchone()[0])
            available_sellable = sellable - already_reserved
            if qty > available_sellable:
                pending_error = DomainError(
                    ERR_INSUFFICIENT_SELLABLE, "可卖份额不足",
                    details={"requested": qty, "sellable": sellable,
                             "reserved": already_reserved,
                             "available_sellable": available_sellable},
                )
            else:
                checks.append({"name": "可卖份额", "pass": True,
                               "sellable": available_sellable})

        if pending_error is not None:
            conn.execute(
                "UPDATE pt_order SET state='REJECTED', reject_reason=?, updated_at=? "
                "WHERE order_id=?",
                (f"{pending_error.code}: {pending_error.message}", now, order_id),
            )
        else:
            if historical_manual:
                conn.execute(
                    "UPDATE pt_cycle SET phase='PRE_OPEN', finished_at=NULL, blocked_reason=?"
                    " WHERE run_date>=? AND phase='DONE'",
                    (f"HISTORICAL_REPLAY:{order_id}", eligible_trade_date),
                )
            conn.execute(
                "UPDATE pt_order SET state='CONFIRMED', reserve_fen=?, reserved_qty=?,"
                " confirmed_at=?, eligible_trade_date=?, updated_at=? WHERE order_id=?",
                (reserve_fen, reserved_qty, confirmed_at, eligible_trade_date, now, order_id),
            )
            conn.execute(
                "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
                " before_json, after_json, occurred_at)"
                " VALUES ('system','ORDER_CONFIRM','order',?,NULL,?,?)",
                (order_id,
                 f'{{"reserve_fen":{reserve_fen},"reserved_qty":{reserved_qty},'
                 f'"eligible_trade_date":"{eligible_trade_date}","checks":{len(checks)}}}', now),
            )
    if pending_error is not None:
        raise pending_error
    return get_order(db_path, order_id)


def prev_trade_date(db_path: str | Path, d: str) -> str:
    """d 之前的上一个开市日（不含 d 当天）。"""
    db_path = Path(db_path)
    try:
        import datetime as _dt
        cur = _dt.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:8]}") - _dt.timedelta(days=1)
        while not is_open(db_path, cur.strftime("%Y%m%d")):
            cur -= _dt.timedelta(days=1)
        return cur.strftime("%Y%m%d")
    except Exception:  # noqa: BLE001
        return d


def _reject(order_id: str, code: str, reason: str, db_path: Path) -> None:
    """订单置 REJECTED 并记录原因。"""
    now = _now()
    with tx(db_path, immediate=True) as conn:
        conn.execute(
            "UPDATE pt_order SET state='REJECTED', reject_reason=?, updated_at=? WHERE order_id=?",
            (f"{code}: {reason}", now, order_id),
        )
        conn.execute(
            "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
            " before_json, after_json, occurred_at)"
            " VALUES ('system','ORDER_REJECT','order',?,NULL,?,?)",
            (order_id, f'{{"code":"{code}","reason":"{reason}"}}', now),
        )


# ── 取消 ──

def cancel_order(db_path: str | Path, order_id: str) -> dict[str, Any]:
    """取消订单并释放预留（DRAFT/CONFIRMED/QUEUED → CANCELLED）。"""
    db_path = Path(db_path)
    order = get_order(db_path, order_id)
    if order["state"] not in _CANCELABLE:
        raise DomainError(ERR_INVALID_STATE,
                          f"状态 {order['state']} 不可取消",
                          details={"order_id": order_id, "state": order["state"]})
    now = _now()
    with tx(db_path, immediate=True) as conn:
        conn.execute(
            "UPDATE pt_order SET state='CANCELLED', reserve_fen=0, reserved_qty=0,"
            " updated_at=? WHERE order_id=?",
            (now, order_id),
        )
        conn.execute(
            "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
            " before_json, after_json, occurred_at)"
            " VALUES ('system','ORDER_CANCEL','order',?,NULL,?,?)",
            (order_id, f'{{"prev_state":"{order["state"]}"}}', now),
        )
    return get_order(db_path, order_id)


# ── 查询 ──

def get_order(db_path: str | Path, order_id: str) -> dict[str, Any]:
    db_path = Path(db_path)
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT order_id, idempotency_key, account_id, source, ts_code, side,"
            " qty, state, reserve_fen, reserved_qty, signal_trade_date, confirmed_at,"
            " eligible_trade_date, reject_reason, created_at, updated_at"
            " FROM pt_order WHERE order_id=?", (order_id,)
        ).fetchone()
    if not row:
        raise DomainError("ORDER_NOT_FOUND", f"订单不存在: {order_id}",
                          details={"order_id": order_id})
    return {
        "order_id": row[0], "idempotency_key": row[1], "account_id": row[2],
        "source": row[3], "ts_code": row[4], "side": row[5], "qty": row[6],
        "state": row[7], "reserve_fen": row[8], "reserved_qty": row[9],
        "signal_trade_date": row[10], "confirmed_at": row[11],
        "eligible_trade_date": row[12], "reject_reason": row[13],
        "created_at": row[14], "updated_at": row[15],
    }


def list_orders(
    db_path: str | Path,
    *,
    state: str | None = None,
    ts_code: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    db_path = Path(db_path)
    sql = (
        "SELECT order_id, source, ts_code, side, qty, state, reserve_fen, reject_reason,"
        " signal_trade_date, eligible_trade_date, created_at FROM pt_order WHERE 1=1"
    )
    params: list[Any] = []
    if state:
        sql += " AND state=?"
        params.append(state)
    if ts_code:
        sql += " AND ts_code=?"
        params.append(ts_code)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(limit)
    with tx(db_path, immediate=False) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"order_id": r[0], "source": r[1], "ts_code": r[2], "side": r[3],
         "qty": r[4], "state": r[5], "reserve_fen": r[6], "reject_reason": r[7],
         "signal_trade_date": r[8], "eligible_trade_date": r[9], "created_at": r[10]}
        for r in rows
    ]
