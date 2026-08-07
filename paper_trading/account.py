"""账户服务：账户初始化 + 期初持仓导入（portfolio.json 预览-确认）。

阶段2 需求：
- 创建账户时输入「当前可用现金」（不代表总资产）
- portfolio.json 导入前展示代码/数量/成本/止损/建仓时间/当前行情/校验错误
- 用户确认的持仓生成 OPENING_POSITION 批次，不倒扣初始化现金
- 已持有超过一个交易日的期初仓立即可卖；当日建仓按交易规则计算可卖日期
- 保存源文件哈希，重复导入同一文件不得重复增加持仓
- 原文件保持不变（只读，作为回滚依据）
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .db import tx
from .errors import DomainError, ERR_ACCOUNT_EXISTS, ERR_INVALID_STATE, ERR_UNKNOWN_ACCOUNT
from .cal import is_open

_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ── 账户 ──

def create_account(db_path: str | Path, initial_cash_fen: int) -> dict[str, Any]:
    """创建唯一账户（account_id=1）。已存在 → ERR_ACCOUNT_EXISTS。
    写入账户 + INITIAL 现金流水（running balance）。"""
    db_path = Path(db_path)
    if not isinstance(initial_cash_fen, int) or initial_cash_fen < 0:
        raise DomainError(
            "INVALID_INITIAL_CASH",
            "初始现金必须为非负整数（分）",
            details={"initial_cash_fen": initial_cash_fen},
        )
    now = _now()
    with tx(db_path, immediate=True) as conn:
        exists = conn.execute("SELECT 1 FROM pt_account WHERE account_id=1").fetchone()
        if exists:
            raise DomainError(
                ERR_ACCOUNT_EXISTS, "纸面账户已存在", retryable=False,
                details={"account_id": 1},
            )
        conn.execute(
            "INSERT INTO pt_account (account_id, initial_cash_fen, status,"
            " config_version, created_at, updated_at) VALUES (1,?,?,1,?,?)",
            (initial_cash_fen, "ACTIVE", now, now),
        )
        if initial_cash_fen > 0:
            conn.execute(
                "INSERT INTO pt_cash_flow (account_id, kind, amount_fen, balance_fen,"
                " ref_id, occurred_at) VALUES (1,'INITIAL',?,?,NULL,?)",
                (initial_cash_fen, initial_cash_fen, now),
            )
    return get_account(db_path)


def get_account(db_path: str | Path) -> dict[str, Any]:
    """读取账户。不存在 → ERR_UNKNOWN_ACCOUNT。"""
    db_path = Path(db_path)
    with tx(db_path, immediate=False) as conn:
        row = conn.execute(
            "SELECT account_id, initial_cash_fen, status, config_version,"
            " created_at, updated_at FROM pt_account WHERE account_id=1"
        ).fetchone()
        if not row:
            raise DomainError(ERR_UNKNOWN_ACCOUNT, "纸面账户不存在", details={"account_id": 1})
        cash = conn.execute(
            "SELECT balance_fen FROM pt_cash_flow WHERE account_id=1"
            " ORDER BY flow_id DESC LIMIT 1"
        ).fetchone()
        return {
            "account_id": row[0],
            "initial_cash_fen": row[1],
            "status": row[2],
            "config_version": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "cash_fen": int(cash[0]) if cash else 0,
        }


def account_exists(db_path: str | Path) -> bool:
    db_path = Path(db_path)
    with tx(db_path, immediate=False) as conn:
        return conn.execute("SELECT 1 FROM pt_account WHERE account_id=1").fetchone() is not None


# ── 期初持仓导入（portfolio.json） ──

def parse_portfolio_json(path: str | Path) -> list[dict[str, Any]]:
    """读取 portfolio.json 的 positions 列表（不做校验）。"""
    p = Path(path)
    if not p.is_file():
        raise DomainError("PORTFOLIO_FILE_NOT_FOUND", f"文件不存在: {p}", details={"path": str(p)})
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise DomainError("PORTFOLIO_PARSE_ERROR", f"JSON 解析失败: {e}", details={"path": str(p)}) from e
    return list(data.get("positions") or [])


def validate_import_item(
    item: dict[str, Any],
    *,
    known_codes: set[str] | None = None,
) -> dict[str, Any]:
    """校验单条导入项。返回 {ts_code, name, cost, shares, stop_loss, opened_at, errors[]}。

    规则：未知代码 / 非整数数量 / 负数 / 缺失成本 → 逐条列出错误，不静默修正。
    """
    code = str(item.get("ts_code") or "").upper().strip()
    errors: list[str] = []
    shares_raw = item.get("shares")
    cost_raw = item.get("cost")
    stop_raw = item.get("stop_loss")

    if not code:
        errors.append("缺失 ts_code")
    elif known_codes is not None and code not in known_codes:
        errors.append(f"未知代码 {code}（本地库无此标的日线）")

    # 数量：必须为正整数（整数股，拒绝 100.5 / -100 / 'abc'）
    if shares_raw is None or shares_raw == "":
        shares = None
        errors.append(f"数量缺失，收到 {shares_raw!r}")
    else:
        try:
            shares_f = float(shares_raw)
            if shares_f != int(shares_f):
                errors.append(f"数量非整数: {shares_raw!r}")
            shares = int(shares_f)
            if shares <= 0:
                errors.append(f"数量必须为正整数，收到 {shares_raw!r}")
        except (TypeError, ValueError):
            shares = None
            errors.append(f"数量非数字: {shares_raw!r}")

    # 成本：必须为 >0 数字
    if cost_raw is None or cost_raw == "":
        cost = None
        errors.append("成本缺失或非法")
    else:
        try:
            cost = float(cost_raw)
            if cost != cost or cost <= 0:  # NaN 或非正
                errors.append(f"成本非法: {cost_raw!r}")
        except (TypeError, ValueError):
            cost = None
            errors.append(f"成本非数字: {cost_raw!r}")

    try:
        stop_loss = float(stop_raw) if stop_raw not in (None, "") else None
    except (TypeError, ValueError):
        stop_loss = None
        if stop_raw not in (None, ""):
            errors.append(f"止损非数字: {stop_raw!r}")

    return {
        "ts_code": code,
        "name": str(item.get("name") or ""),
        "cost": cost,
        "shares": shares,
        "stop_loss": stop_loss,
        "opened_at": str(item.get("opened_at") or ""),
        "note": str(item.get("note") or ""),
        "errors": errors,
        "valid": not errors,
    }


def preview_import(
    db_path: str | Path,
    portfolio_path: str | Path,
    *,
    known_codes: set[str] | None = None,
) -> dict[str, Any]:
    """导入前预览：逐条校验 + 当前行情。返回 items + 汇总（valid/invalid 计数）。"""
    db_path = Path(db_path)
    items_raw = parse_portfolio_json(portfolio_path)
    import sqlite3

    codes_need = [it.get("ts_code") for it in items_raw if it.get("ts_code")]
    if known_codes is None:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT DISTINCT ts_code FROM daily").fetchall()
            known_codes = {r[0] for r in rows}
        finally:
            conn.close()

    items: list[dict[str, Any]] = []
    for raw in items_raw:
        it = validate_import_item(raw, known_codes=known_codes)
        # 当前行情（最近收盘）
        if it["ts_code"]:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT close, trade_date FROM daily WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
                    (it["ts_code"],),
                ).fetchone()
                it["last_close"] = float(row[0]) if row else None
                it["last_date"] = str(row[1]) if row else None
            finally:
                conn.close()
        items.append(it)
    valid = [i for i in items if i["valid"]]
    return {
        "source_file": str(Path(portfolio_path).resolve()),
        "source_hash": _hash_file(Path(portfolio_path)),
        "total": len(items),
        "valid_count": len(valid),
        "invalid_count": len(items) - len(valid),
        "items": items,
        "has_invalid": any(i["errors"] for i in items),
    }


def commit_import(
    db_path: str | Path,
    portfolio_path: str | Path,
    *,
    import_hash: str | None = None,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """确认导入：为每条有效持仓生成 OPENING_POSITION 批次（不倒扣现金）。

    幂等：按 source_hash 检查——同哈希已导入过 → 不重复增加持仓。
    返回 {"imported": n, "skipped_existing": bool, "positions": [...]}
    """
    db_path = Path(db_path)
    src_hash = import_hash or _hash_file(Path(portfolio_path))
    if not account_exists(db_path):
        raise DomainError(ERR_UNKNOWN_ACCOUNT, "请先创建账户再导入持仓")

    # 幂等检查：同哈希是否已导入（审计事件 action='PORTFOLIO_IMPORT' 且 after_json 含 hash）
    with tx(db_path, immediate=False) as conn:
        dup = conn.execute(
            "SELECT 1 FROM pt_audit_event WHERE action='PORTFOLIO_IMPORT'"
            " AND after_json LIKE ? LIMIT 1", (f"%{src_hash}%",)
        ).fetchone()
    if dup:
        return {"imported": 0, "skipped_existing": True, "positions": []}

    preview = preview_import(db_path, portfolio_path)
    valid = [i for i in preview["items"] if i["valid"]]
    now = _now()

    # as_of_date 默认为当前最新交易日
    if as_of_date is None:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
            as_of_date = str(row[0]) if row and row[0] else datetime.now(_TZ).strftime("%Y%m%d")
        finally:
            conn.close()

    # 事务外预计算每条的可卖日（next_open 会开新连接，不能在写事务内调用）
    # 语义：期初仓 opened_at 早于 as_of_date（已持有>1日）→ 立即卖（=as_of_date）
    #       当日/更晚建仓 → T+1：as_of_date 之后的下一个开市日
    sellable_by_code: dict[str, str] = {}
    for it in valid:
        code = it["ts_code"]
        opened = it.get("opened_at") or ""
        if opened and opened[:10].replace("-", "") < as_of_date:
            sellable_by_code[code] = as_of_date
        else:
            # T+1：从 as_of_date 次日开始找下一个开市日
            import datetime as _dt
            from .cal import is_open

            cur = _dt.date.fromisoformat(f"{as_of_date[:4]}-{as_of_date[4:6]}-{as_of_date[6:8]}") \
                + _dt.timedelta(days=1)
            d = cur.strftime("%Y%m%d")
            while not is_open(db_path, d):
                cur += _dt.timedelta(days=1)
                d = cur.strftime("%Y%m%d")
            sellable_by_code[code] = d

    imported: list[dict[str, Any]] = []
    with tx(db_path, immediate=True) as conn:
        for it in valid:
            code = it["ts_code"]
            cost = it["cost"]
            shares = it["shares"]
            cost_micro = int(round(cost * 1_000_000))
            order_id = f"OPENING-{code}-{as_of_date}"
            fill_id = f"OF-{code}-{as_of_date}"
            # 期初仓订单（OPENING 来源，直接 FILLED，无真实成交/资金预留）
            conn.execute(
                "INSERT OR IGNORE INTO pt_order (order_id, idempotency_key, account_id,"
                " source, ts_code, side, qty, state, reserve_fen, created_at, updated_at)"
                " VALUES (?,?,1,'OPENING_IMPORT',?,'BUY',?,?,0,?,?)",
                (order_id, f"opening-{code}-{as_of_date}", code, shares, "FILLED", now, now),
            )
            # 期初仓：生成 OPENING fill（无真实成交，模型版本标记 OPENING_IMPORT）
            conn.execute(
                "INSERT OR IGNORE INTO pt_fill (fill_id, order_id, ref_open_price_micro,"
                " fill_price_micro, qty, commission_fen, tax_fen, fill_model_version,"
                " quote_revision, filled_at) VALUES (?,?,?,?,?,0,0,'OPENING_IMPORT','N/A',?)",
                (fill_id, order_id, cost_micro, cost_micro, shares, now),
            )
            sellable = sellable_by_code[code]
            conn.execute(
                "INSERT INTO pt_position_lot (account_id, ts_code, buy_fill_id,"
                " remaining_qty, cost_price_micro, sellable_date, created_at)"
                " VALUES (1,?,?,?,?,?,?)",
                (code, fill_id, shares, cost_micro, sellable, now),
            )
            imported.append({
                "ts_code": code,
                "shares": shares,
                "cost": cost,
                "cost_micro": cost_micro,
                "sellable_date": sellable,
            })
        # 审计事件：记录导入（含源文件哈希，供幂等）
        conn.execute(
            "INSERT INTO pt_audit_event (actor, action, entity_type, entity_id,"
            " before_json, after_json, occurred_at)"
            " VALUES ('system','PORTFOLIO_IMPORT','portfolio',?,?,?,?)",
            (
                str(Path(portfolio_path).resolve()),
                json.dumps({"valid_count": len(valid), "invalid_count": preview["invalid_count"]},
                           ensure_ascii=False),
                json.dumps({"source_hash": src_hash, "imported": len(valid),
                            "as_of": as_of_date}, ensure_ascii=False),
                now,
            ),
        )
    return {"imported": len(imported), "skipped_existing": False, "positions": imported}


def opening_equity(db_path: str | Path) -> dict[str, Any]:
    """期初权益 = 初始化现金 + 期初持仓收盘市值。"""
    db_path = Path(db_path)
    acct = get_account(db_path)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT l.ts_code, SUM(l.remaining_qty), d.close"
            " FROM pt_position_lot l JOIN daily d ON d.ts_code=l.ts_code"
            " WHERE l.account_id=1"
            " AND d.trade_date=(SELECT MAX(trade_date) FROM daily WHERE ts_code=l.ts_code)"
            " GROUP BY l.ts_code"
        ).fetchall()
    finally:
        conn.close()
    market_value_fen = 0
    for code, qty, close in rows:
        market_value_fen += int(round(float(qty) * float(close) * 100))  # 股×元×100=分
    return {
        "cash_fen": acct["cash_fen"],
        "market_value_fen": market_value_fen,
        "total_equity_fen": acct["cash_fen"] + market_value_fen,
        "positions": len(rows),
    }
