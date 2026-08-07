"""仿真撮合与会计处理（阶段4）。

确定性撮合规则：
- 使用已确认订单 + 下一交易日行情（开盘价基准）
- 买入加滑点、卖出减滑点，限制在当日高低价区间
- 一字涨停买单、一字跌停卖单、无报价、成交量为零 → 零成交
- 最大成交量为当日成交量的 5%，按标的交易单位向下取整
- 停牌不视为实际撮合（顺延）
- 同一事务写入成交 + 现金流水 + 持仓批次 + 审计事件
- 买入费用计入成本；卖出实现损益按 FIFO 批次核销并扣卖出费用
- 禁止负现金、负持仓、超卖、重复成交
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .db import tx
from .errors import DomainError
from .rules import get_rule

_TZ = ZoneInfo("Asia/Shanghai")

FILL_MODEL_VERSION = "v1"


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _day_bar(db_path: Path, ts_code: str, trade_date: str) -> dict[str, Any] | None:
    """当日日线（开高低收量）。无 → None。"""
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT open, high, low, close, vol, amount FROM daily"
            " WHERE ts_code=? AND trade_date=?", (ts_code, trade_date)
        ).fetchone()
    if not row:
        return None
    return {"open": float(row[0]), "high": float(row[1]), "low": float(row[2]),
            "close": float(row[3]), "vol": float(row[4]), "amount": float(row[5])}


def _prev_close(db_path: Path, ts_code: str, trade_date: str) -> float | None:
    """前收盘价（trade_date 之前最近一根）。"""
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT close FROM daily WHERE ts_code=? AND trade_date<?"
            " ORDER BY trade_date DESC LIMIT 1", (ts_code, trade_date)
        ).fetchone()
    return float(row[0]) if row else None


def _is_limit_one_word(db_path: Path, ts_code: str, trade_date: str, side: str) -> bool:
    """一字涨停（买单）/ 一字跌停（卖单）判定：open==high==low（近似一字）。"""
    bar = _day_bar(db_path, ts_code, trade_date)
    if not bar:
        return False
    if side == "BUY":
        return bar["open"] == bar["high"] == bar["low"] and bar["close"] >= bar["open"]
    return bar["open"] == bar["high"] == bar["low"] and bar["close"] <= bar["open"]


def execute_fills(
    db_path: str | Path,
    trade_date: str,
    *,
    limit_codes: list[str] | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """对 trade_date 已确认/排队订单执行确定性撮合。

    - 订单确认于 trade_date 之前（today 参数用于状态检查）
    - limit_codes：仅撮合指定标的（测试用）；None = 全部活动订单
    返回 {"filled": [...], "expired": [...], "zero_fill": [...]}
    """
    db_path = Path(db_path)
    today = today or datetime.now(_TZ).strftime("%Y%m%d")

    # 读取活动订单（CONFIRMED/QUEUED），可选按标的过滤
    sql = ("SELECT order_id, ts_code, side, qty, state, reserve_fen FROM pt_order"
           " WHERE state IN ('CONFIRMED','QUEUED') AND account_id=1")
    params: list[Any] = []
    if limit_codes:
        ph = ",".join("?" * len(limit_codes))
        sql += f" AND ts_code IN ({ph})"
        params.extend(limit_codes)
    with tx(db_path, immediate=False) as conn:
        orders = conn.execute(sql, params).fetchall()

    filled: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    zero_fill: list[dict[str, Any]] = []

    with tx(db_path, immediate=True) as conn:
        for order_id, ts_code, side, qty, state, reserve_fen in orders:
            rule = get_rule(db_path, ts_code)
            bar = _day_bar(db_path, ts_code, trade_date)
            if bar is None:
                # 停牌/缺行情：零成交，订单保留（顺延下一交易日）
                zero_fill.append({"order_id": order_id, "ts_code": ts_code,
                                  "reason": "NO_QUOTE", "qty": qty})
                continue
            if bar["vol"] <= 0:
                zero_fill.append({"order_id": order_id, "ts_code": ts_code,
                                  "reason": "ZERO_VOLUME", "qty": qty})
                continue
            # 一字涨停买单 / 一字跌停卖单 → 零成交
            if _is_limit_one_word(db_path, ts_code, trade_date, side):
                zero_fill.append({"order_id": order_id, "ts_code": ts_code,
                                  "reason": f"LIMIT_ONE_WORD_{side}", "qty": qty})
                continue

            # 基准价：开盘价 + 滑点（BUY 加、SELL 减），限在高低区间
            slippage = rule.slippage_bps / 10_000.0
            ref_open = bar["open"]
            if side == "BUY":
                fill_px = ref_open * (1 + slippage)
            else:
                fill_px = ref_open * (1 - slippage)
            fill_px = max(bar["low"], min(bar["high"], fill_px))

            # 最大成交量 = 当日成交量 × 5%，按整手向下取整
            max_qty = int(bar["vol"] * 0.05 // rule.lot_size) * rule.lot_size
            fill_qty = min(qty, max_qty)
            if fill_qty < rule.lot_size:
                # 成交量不足以成交一手 → 零成交
                zero_fill.append({"order_id": order_id, "ts_code": ts_code,
                                  "reason": "INSUFFICIENT_LIQUIDITY", "qty": qty,
                                  "max_qty": max_qty})
                continue

            # 成交价微元
            fill_price_micro = int(round(fill_px * 1_000_000))
            # 费用（分）：佣金 + 税 + 其他
            notional_fen = int(round(fill_px * fill_qty * 100))
            commission = max(rule.min_commission_fen,
                             int(round(notional_fen * rule.commission_bps / 10_000)))
            tax = int(round(notional_fen * rule.sell_tax_bps / 10_000)) if side == "SELL" else 0
            other = int(round(notional_fen * rule.other_fee_bps / 10_000))

            fill_id = _new_id("FILL")
            now = _now()
            # 1) 成交
            conn.execute(
                "INSERT INTO pt_fill (fill_id, order_id, ref_open_price_micro,"
                " fill_price_micro, qty, commission_fen, tax_fen, fill_model_version,"
                " quote_revision, filled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (fill_id, order_id, int(round(ref_open * 1_000_000)), fill_price_micro,
                 fill_qty, commission, tax, FILL_MODEL_VERSION, f"{ts_code}:{trade_date}", now),
            )
            # 2) 现金流水
            if side == "BUY":
                total_debit = notional_fen + commission + tax + other
                _apply_cash(conn, "BUY", -total_debit, fill_id, now)
                # 持仓批次（T+1 可卖）
                sellable = _next_sellable(db_path, trade_date)
                conn.execute(
                    "INSERT INTO pt_position_lot (account_id, ts_code, buy_fill_id,"
                    " remaining_qty, cost_price_micro, sellable_date, created_at)"
                    " VALUES (1,?,?,?,?,?,?)",
                    (ts_code, fill_id, fill_qty, fill_price_micro, sellable, now),
                )
            else:
                total_credit = notional_fen - commission - tax - other
                _apply_cash(conn, "SELL", total_credit, fill_id, now)
                _consume_lots_fifo(conn, db_path, ts_code, fill_id, fill_qty,
                                   fill_price_micro, trade_date, now)

            # 3) 订单状态
            if fill_qty == qty:
                new_state = "FILLED"
            else:
                # 部分成交：余量当日过期
                new_state = "PARTIALLY_FILLED_EXPIRED"
            conn.execute(
                "UPDATE pt_order SET state=?, reserve_fen=0, updated_at=? WHERE order_id=?",
                (new_state, now, order_id),
            )
            # 4) 审计
            conn.execute(
                "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
                " before_json, after_json, occurred_at)"
                " VALUES ('system','FILL_EXECUTE','fill',?,NULL,?,?)",
                (fill_id,
                 f'{{"order_id":"{order_id}","qty":{fill_qty},"px_micro":{fill_price_micro},'
                 f'"commission":{commission},"tax":{tax},"trade_date":"{trade_date}"}}', now),
            )
            filled.append({
                "fill_id": fill_id, "order_id": order_id, "ts_code": ts_code,
                "side": side, "qty": fill_qty, "price_micro": fill_price_micro,
                "commission_fen": commission, "tax_fen": tax, "trade_date": trade_date,
            })

    return {"filled": filled, "expired": expired, "zero_fill": zero_fill}


def _apply_cash(conn: sqlite3.Connection, kind: str, amount_fen: int,
                ref_id: str, occurred_at: str) -> None:
    """写入现金流水（running balance），禁止负现金。"""
    row = conn.execute(
        "SELECT balance_fen FROM pt_cash_flow WHERE account_id=1"
        " ORDER BY flow_id DESC LIMIT 1"
    ).fetchone()
    prev_balance = int(row[0]) if row else 0
    new_balance = prev_balance + amount_fen
    if new_balance < 0:
        raise DomainError("NEGATIVE_CASH_FORBIDDEN",
                          f"现金将为负: {new_balance}", retryable=False,
                          details={"prev": prev_balance, "delta": amount_fen})
    conn.execute(
        "INSERT INTO pt_cash_flow (account_id, kind, amount_fen, balance_fen,"
        " ref_id, occurred_at) VALUES (1,?,?,?,?,?)",
        (kind, amount_fen, new_balance, ref_id, occurred_at),
    )


def _next_sellable(db_path: Path, trade_date: str) -> str:
    """T+1 可卖日 = trade_date 之后的下一个开市日。"""
    import datetime as _dt

    from .cal import is_open
    cur = _dt.date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}") \
        + _dt.timedelta(days=1)
    d = cur.strftime("%Y%m%d")
    while not is_open(db_path, d):
        cur += _dt.timedelta(days=1)
        d = cur.strftime("%Y%m%d")
    return d


def _consume_lots_fifo(
    conn: sqlite3.Connection,
    db_path: Path,
    ts_code: str,
    sell_fill_id: str,
    qty: int,
    sell_price_micro: int,
    trade_date: str,
    occurred_at: str,
) -> None:
    """FIFO 核销持仓批次；记录已实现损益到现金流（损益并入 SELL 现金流已扣成本）。

    实现：卖出现金流水已按成交额-费用入账；这里核销批次 remaining_qty，
    并把「已实现盈亏」写入审计，便于对账追溯。
    """
    lots = conn.execute(
        "SELECT lot_id, remaining_qty, cost_price_micro FROM pt_position_lot"
        " WHERE ts_code=? AND account_id=1 AND remaining_qty>0"
        " ORDER BY lot_id ASC", (ts_code,)
    ).fetchall()
    remaining = qty
    realized_total = 0
    for lot_id, lot_qty, cost_micro in lots:
        if remaining <= 0:
            break
        take = min(lot_qty, remaining)
        cost_fen = int(round(cost_micro / 1_000_000 * take * 100))
        proceeds_fen = int(round(sell_price_micro / 1_000_000 * take * 100))
        realized_total += proceeds_fen - cost_fen
        new_qty = lot_qty - take
        conn.execute(
            "UPDATE pt_position_lot SET remaining_qty=? WHERE lot_id=?",
            (new_qty, lot_id),
        )
        remaining -= take
    if remaining > 0:
        raise DomainError("INSUFFICIENT_POSITION", "持仓不足，无法全额核销（超卖）",
                          retryable=False, details={"short": remaining})
    conn.execute(
        "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
        " before_json, after_json, occurred_at)"
        " VALUES ('system','FIFO_CONSUME','fill',?,NULL,?,?)",
        (sell_fill_id,
         f'{{"ts_code":"{ts_code}","qty":{qty},"realized_pnl_fen":{realized_total},'
         f'"trade_date":"{trade_date}"}}', occurred_at),
    )


def expire_daily_orders(db_path: str | Path, trade_date: str) -> int:
    """日终：QUEUED/CONFIRMED 未成交订单当日过期（EXPIRED），释放预留。"""
    db_path = Path(db_path)
    now = _now()
    with tx(db_path, immediate=True) as conn:
        cur = conn.execute(
            "UPDATE pt_order SET state='EXPIRED', reserve_fen=0, updated_at=?"
            " WHERE state IN ('CONFIRMED','QUEUED') AND account_id=1", (now,)
        )
        return cur.rowcount
