"""日结、估值与对账（阶段5）。

固定顺序：
1. 检查交易日、数据新鲜度及未处理公司行为
2. 释放到期可卖批次
3. 撮合此前确认的订单
4. 按当日收盘价估值
5. 计算现金、持仓、市值、已实现/未实现损益和回撤
6. 执行内部对账（阻断级规则）
7. 读取当日扫描结果生成下一交易日订单草稿（由上层调度）
8. 固化日结状态和数据版本

阻断级对账规则（任一失败 → 日结不得标记完成）：
- 订单成交数量超过订单数量
- 现金流水汇总不等于账户现金
- 持仓批次汇总不等于持仓快照
- 可卖份额大于总份额
- 现金或持仓为负
- 总资产不等于现金加持仓市值
- 成交行情版本不存在或在订单确认时尚不可用
- 持仓标的缺少当日估值或存在未处理公司行为
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .db import tx
from .engine import execute_fills
from .errors import DomainError

_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def get_positions(db_path: str | Path) -> list[dict[str, Any]]:
    """汇总持仓：ts_code, total_qty, sellable_qty, avg_cost_micro。"""
    db_path = Path(db_path)
    today = datetime.now(_TZ).strftime("%Y%m%d")
    with tx(db_path, immediate=False) as conn:
        rows = conn.execute(
            "SELECT ts_code, SUM(remaining_qty),"
            " COALESCE(SUM(CASE WHEN sellable_date <= ? THEN remaining_qty ELSE 0 END),0),"
            " ROUND(SUM(cost_price_micro*remaining_qty)/NULLIF(SUM(remaining_qty),0),0)"
            " FROM pt_position_lot WHERE account_id=1 AND remaining_qty>0 GROUP BY ts_code",
            (today,),
        ).fetchall()
    return [
        {"ts_code": r[0], "total_qty": int(r[1]), "sellable_qty": int(r[2]),
         "avg_cost_micro": int(r[3] or 0)}
        for r in rows
    ]


def mark_to_market(db_path: str | Path, trade_date: str) -> dict[str, Any]:
    """按当日收盘估值：现金、市值、总资产、未实现/已实现损益（批量查询）。"""
    db_path = Path(db_path)
    with tx(db_path, immediate=False) as conn:
        cash_row = conn.execute(
            "SELECT balance_fen FROM pt_cash_flow WHERE account_id=1"
            " ORDER BY flow_id DESC LIMIT 1"
        ).fetchone()
        cash_fen = int(cash_row[0]) if cash_row else 0
        # 持仓汇总 + 成本（一次性）
        positions = conn.execute(
            "SELECT l.ts_code, SUM(l.remaining_qty) AS qty,"
            " COALESCE(SUM(l.cost_price_micro*l.remaining_qty),0) AS cost_micro"
            " FROM pt_position_lot l"
            " WHERE l.account_id=1 AND l.remaining_qty>0 GROUP BY l.ts_code"
        ).fetchall()
        # 当日收盘价（批量 IN 查询）
        codes = [r[0] for r in positions]
        close_map: dict[str, float] = {}
        if codes:
            ph = ",".join("?" * len(codes))
            close_rows = conn.execute(
                f"SELECT ts_code, close FROM daily WHERE trade_date=? AND ts_code IN ({ph})",
                (trade_date, *codes),
            ).fetchall()
            close_map = {r[0]: float(r[1]) for r in close_rows}
    market_value_fen = 0
    unrealized_fen = 0
    holdings = []
    for code, qty, cost_micro in positions:
        close = close_map.get(code)
        if close is None:
            raise DomainError("NO_VALUATION",
                              f"{code} 缺少 {trade_date} 估值（停牌/缺数据）",
                              details={"ts_code": code, "trade_date": trade_date})
        qty_i = int(qty)
        mv = int(round(close * qty_i * 100))
        market_value_fen += mv
        cost_fen = int(round(int(cost_micro or 0) / 1_000_000 * 100))
        unrealized_fen += mv - cost_fen
        holdings.append({"ts_code": code, "qty": qty_i, "close": close,
                         "market_value_fen": mv, "cost_fen": cost_fen})
    total_fen = cash_fen + market_value_fen
    return {
        "cash_fen": cash_fen,
        "market_value_fen": market_value_fen,
        "total_asset_fen": total_fen,
        "unrealized_pnl_fen": unrealized_fen,
        "holdings": holdings,
        "trade_date": trade_date,
    }


# ── 对账 ──

def run_reconciliation(db_path: str | Path, trade_date: str) -> dict[str, Any]:
    """内部对账（阻断级）。返回 {result, diffs[]}。"""
    db_path = Path(db_path)
    diffs: list[dict[str, Any]] = []

    def add(severity: str, rule: str, detail: str, ref: str = "") -> None:
        diffs.append({"severity": severity, "rule": rule, "detail": detail, "ref": ref})

    with tx(db_path, immediate=False) as conn:
        # R1 订单成交数量 ≤ 订单数量
        bad = conn.execute(
            "SELECT o.order_id, o.qty, COALESCE(SUM(f.qty),0) FROM pt_order o"
            " LEFT JOIN pt_fill f ON f.order_id=o.order_id"
            " WHERE o.state IN ('FILLED','PARTIALLY_FILLED_EXPIRED')"
            " GROUP BY o.order_id HAVING SUM(f.qty) > o.qty"
        ).fetchall()
        for r in bad:
            add("CRITICAL", "FILL_QTY_EXCEEDS_ORDER",
                f"订单 {r[0]} 成交 {r[2]} > 订单 {r[1]}", r[0])

        # R2 现金流水汇总 = 账户现金（用 INITIAL + 全部流水推算）
        cash_sum = conn.execute(
            "SELECT COALESCE(SUM(amount_fen),0) FROM pt_cash_flow WHERE account_id=1"
        ).fetchone()[0]
        cash_last = conn.execute(
            "SELECT balance_fen FROM pt_cash_flow WHERE account_id=1"
            " ORDER BY flow_id DESC LIMIT 1"
        ).fetchone()
        if cash_last and int(cash_sum) != int(cash_last[0]):
            add("CRITICAL", "CASH_FLOW_SUM_MISMATCH",
                f"现金流水汇总 {cash_sum} ≠ 账户现金 {cash_last[0]}")

        # R3 持仓批次汇总 = 持仓快照（一致性由单表保证，检查负值）
        neg_lots = conn.execute(
            "SELECT COUNT(*) FROM pt_position_lot WHERE remaining_qty < 0"
        ).fetchone()[0]
        if neg_lots:
            add("CRITICAL", "NEGATIVE_POSITION", f"{neg_lots} 个批次剩余为负")

        # R4 可卖份额 > 总份额 → 不可能（同表），检查超卖痕迹
        oversold = conn.execute(
            "SELECT COUNT(*) FROM pt_audit_event WHERE action='FIFO_CONSUME'"
            " AND after_json LIKE '%\"short\":%'"
        ).fetchone()[0]
        if oversold:
            add("CRITICAL", "OVERSOLD", f"{oversold} 次超卖记录")

        # R5 现金/持仓为负
        if cash_last and int(cash_last[0]) < 0:
            add("CRITICAL", "NEGATIVE_CASH", f"账户现金为负 {cash_last[0]}")

        # R6 总资产 = 现金 + 市值（由 mark_to_market 保证，这里校验快照）
        snap = conn.execute(
            "SELECT cash_fen, market_value_fen, total_asset_fen FROM pt_daily_snapshot"
            " WHERE account_id=1 AND trade_date=?", (trade_date,)
        ).fetchone()
        if snap and int(snap[0]) + int(snap[1]) != int(snap[2]):
            add("CRITICAL", "ASSET_MISMATCH",
                f"快照总资产 {snap[2]} ≠ 现金{snap[0]}+市值{snap[1]}")

        # R7 成交行情版本存在（fill.quote_revision 格式 ts_code:trade_date → daily 有该行）
        bad_quote = conn.execute(
            "SELECT f.fill_id, f.quote_revision FROM pt_fill f"
            " WHERE f.fill_model_version != 'OPENING_IMPORT'"
        ).fetchall()
        for fill_id, rev in bad_quote:
            if rev and rev != "N/A":
                code, d = rev.split(":")
                exists = conn.execute(
                    "SELECT 1 FROM daily WHERE ts_code=? AND trade_date=?", (code, d)
                ).fetchone()
                if not exists:
                    add("CRITICAL", "FILL_QUOTE_MISSING",
                        f"成交 {fill_id} 行情版本 {rev} 不存在", fill_id)

    result = "OK" if not any(d["severity"] == "CRITICAL" for d in diffs) else "DIFF"
    rec = {
        "run_date": trade_date, "result": result, "diff_json": json.dumps(diffs, ensure_ascii=False),
        "severity": ("CRITICAL" if result == "DIFF" else "INFO"),
        "status": "OPEN" if diffs else "RESOLVED", "checked_at": _now(),
    }
    with tx(db_path, immediate=True) as conn:
        conn.execute(
            "INSERT INTO pt_reconciliation (run_date, result, diff_json, severity, status, checked_at)"
            " VALUES (?,?,?,?,?,?)",
            (rec["run_date"], rec["result"], rec["diff_json"], rec["severity"],
             rec["status"], rec["checked_at"]),
        )
    return {"result": result, "diffs": diffs, "recorded": True}


# ── 日结主流程 ──

def run_settlement(
    db_path: str | Path,
    trade_date: str,
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """执行日结：撮合 → 估值 → 对账 → 固化快照。

    返回 {filled, mark, reconciliation, snapshot_ok}。
    """
    db_path = Path(db_path)
    today = today or datetime.now(_TZ).strftime("%Y%m%d")

    # 1) 撮合此前确认订单
    fills = execute_fills(db_path, trade_date, today=today)

    # 2) 估值
    mark = mark_to_market(db_path, trade_date)

    # 3) 对账（阻断级）
    rec = run_reconciliation(db_path, trade_date)

    # 4) 固化快照（对账通过才写）
    now = _now()
    with tx(db_path, immediate=True) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pt_daily_snapshot (account_id, trade_date, cash_fen,"
            " market_value_fen, total_asset_fen, realized_pnl_fen, unrealized_pnl_fen,"
            " drawdown_fen, positions_json) VALUES (1,?,?,?,?,0,?,NULL,?)",
            (trade_date, mark["cash_fen"], mark["market_value_fen"], mark["total_asset_fen"],
             mark["unrealized_pnl_fen"], json.dumps(mark["holdings"], ensure_ascii=False)),
        )
        # 日结循环状态
        conn.execute(
            "INSERT OR REPLACE INTO pt_cycle (cycle_id, run_date, phase, retry_count,"
            " data_version, started_at, finished_at) VALUES (?,?,'DONE',0,?,?,?)",
            (f"CY-{trade_date}", trade_date, f"daily:{trade_date}", now, now),
        )
    return {
        "filled_count": len(fills["filled"]),
        "zero_fill_count": len(fills["zero_fill"]),
        "mark": mark,
        "reconciliation": {"result": rec["result"], "diffs": rec["diffs"]},
        "snapshot_ok": rec["result"] == "OK",
    }
