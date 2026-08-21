"""legacy 纸面交易路由（G2 拆路由第 4 步）。
共享状态从 ab_screener.api.legacy_state import；领域模块（paper_trading / research /
scan_spawn 等）函数内延迟 import，保持与原实现一致。
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
import pandas as pd
from ab_screener.api.legacy_state import (
    _DB,
    _paper_enabled,
    _BUILD_VERSION,
    _LOGGER,
    _PARENT,
    _store,
    _LAB_TASKS,
    _LAB_LOCK,
    _LAB_TASKS_MAX,
    _LAB_STORE,
    _SYNC_LOCK,
    _SYNC_STATE,
    _BT_LOCK,
    _BT_TASKS,
    _BT_TASKS_MAX,
)

router = APIRouter(tags=["legacy"])

# ── 纸面交易 API（paper_trading 领域模块）──


def _paper_err(e: Exception) -> None:
    """DomainError → 结构化错误响应 {code, message, details, retryable}（raise）。"""
    from paper_trading.errors import DomainError

    if isinstance(e, DomainError):
        raise HTTPException(status_code=409 if not e.retryable else 429, detail=e.to_dict())
    from tushare_init import sanitize_error
    raise HTTPException(status_code=500, detail={
        "code": "INTERNAL_ERROR", "message": sanitize_error(e)[:300],
        "details": {}, "retryable": False,
    })


def _paper_write(key: str | None, operation: str, payload: dict, callback):
    """所有纸面交易 POST 的统一持久化幂等边界。"""
    from paper_trading.idempotency import execute_idempotent

    return execute_idempotent(_DB, key or "", operation, payload, callback)


@router.get("/api/paper/account")
def paper_account():
    """读取纸面账户（无 → 404）。"""
    from paper_trading.account import get_account

    try:
        return get_account(_DB)
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/account")
def paper_create_account(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """创建唯一纸面账户：{initial_cash_fen: int}。已存在 → 409。"""
    from paper_trading.account import create_account

    try:
        fen = body.get("initial_cash_fen")
        return _paper_write(
            idempotency_key, "paper.account.create", body,
            lambda: create_account(_DB, int(fen) if fen is not None else 0),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.get("/api/paper/dashboard")
def paper_dashboard():
    """账户摘要 + 持仓 + 期初权益 + 风险状态。"""
    from paper_trading.account import get_account, opening_equity
    from paper_trading.errors import ERR_UNKNOWN_ACCOUNT, DomainError

    try:
        from ab_screener.data.paper_query import load_dashboard_extras

        acct = get_account(_DB)
        eq = opening_equity(_DB)
        extras = load_dashboard_extras(_DB)
        return {
            "account": acct,
            "equity": eq,
            "equity_curve": extras["equity_curve"],
            "guide": __import__(
                "paper_trading.guidance", fromlist=["build_guide"]
            ).build_guide(_DB),
            "risk": {
                "gross_exposure_limit_pct": "80",
                "cash_buffer_pct": "10",
                "daily_buy_limit_pct": "20",
                "single_instrument_limit_pct": "10",
                "reserved_cash_fen": extras["reserved_cash_fen"],
                "reserved_sell_qty": extras["reserved_sell_qty"],
            },
            "unresolved_reconciliation_count": extras["unresolved_reconciliation_count"],
            "paper_notice": "纸面仿真，不会向券商下单",
        }
    except DomainError as e:
        if e.code == ERR_UNKNOWN_ACCOUNT:
            from paper_trading.guidance import build_guide
            return {"account": None, "equity": None, "guide": build_guide(_DB),
                    "paper_notice": "纸面仿真，不会向券商下单"}
        _paper_err(e)
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


def _resolve_import_path(raw: str | None) -> str:
    """纸面导入路径白名单（2026-08-16 整改：修复任意文件读取）。

    仅允许 runtime/portfolio.json；拒绝绝对路径、.. 穿越与其它文件名。
    """
    if not raw:
        return str(_PARENT / "runtime" / "portfolio.json")
    try:
        resolved = Path(raw).resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="path 无效") from exc
    runtime_dir = (_PARENT / "runtime").resolve()
    if not resolved.is_relative_to(runtime_dir) or resolved.name != "portfolio.json":
        raise HTTPException(
            status_code=400,
            detail="仅允许导入 runtime/portfolio.json",
        )
    return str(resolved)


@router.post("/api/paper/import/preview")
def paper_import_preview(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """导入前预览：{path: portfolio.json 路径} → 逐条校验 + 行情。"""
    from paper_trading.account import preview_import

    try:
        path = _resolve_import_path(body.get("path"))
        return _paper_write(
            idempotency_key, "paper.import.preview", body,
            lambda: preview_import(_DB, path),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/import/commit")
def paper_import_commit(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """确认导入：{path, as_of_date?} → 生成 OPENING 批次（幂等）。"""
    from paper_trading.account import commit_import

    try:
        path = _resolve_import_path(body.get("path"))
        return _paper_write(
            idempotency_key, "paper.import.commit", body,
            lambda: commit_import(_DB, path, as_of_date=body.get("as_of_date")),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.get("/api/paper/gates/status")
def paper_gates_status():
    """门禁状态：运行时新鲜度 + 领域表就绪。"""
    from market_regime import data_freshness

    try:
        fresh = data_freshness(_store.max_trade_date("daily") or "", store=_store)
    except Exception:  # noqa: BLE001
        fresh = {"label": "未知", "is_stale": True}
    try:
        from paper_trading.migrations import current_schema_version

        sv = current_schema_version(_DB)
    except Exception:  # noqa: BLE001
        sv = 0
    return {
        "paper_enabled": _paper_enabled(),
        "schema_version": sv,
        "runtime_freshness": fresh,
        "real_data_gate": _latest_gate_status(),
    }


def _latest_gate_status() -> dict:
    from ab_screener.data.paper_query import latest_gate_status

    return latest_gate_status(_DB)


@router.get("/api/paper/orders")
def paper_orders(state: str | None = None, ts_code: str | None = None, limit: int = 50):
    """查询订单：按状态/标的过滤。"""
    from paper_trading.orders import list_orders

    try:
        return {"orders": list_orders(_DB, state=state, ts_code=ts_code, limit=limit)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.get("/api/paper/trading-calendar")
def paper_trading_calendar(start: str, end: str):
    """本地交易日历与账本允许日期边界。"""
    from paper_trading.guidance import trading_calendar

    try:
        return trading_calendar(_DB, start=start, end=end)
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/orders/review")
def paper_review_order(body: dict):
    """只读订单预览；不需要幂等键且不会创建业务记录。"""
    from paper_trading.guidance import review_order

    try:
        return review_order(
            _DB,
            scope=body.get("scope") or "ACCOUNT",
            side=body.get("side") or "BUY",
            mode=body.get("mode") or "MANUAL_HISTORY",
            ts_code=body.get("ts_code") or "",
            qty=int(body.get("qty") or 0),
            execution_trade_date=body.get("execution_trade_date") or "",
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/orders/drafts")
def paper_create_draft(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """创建草稿：{side:'BUY', ts_code, trade_date, suggested_pos_pct?, qty?} 或 {side:'SELL', ts_code, qty}。"""
    from paper_trading.orders import (
        create_buy_draft,
        create_historical_buy_draft,
        create_sell_draft,
    )

    try:
        side = (body.get("side") or "BUY").upper()
        if side == "BUY":
            if str(body.get("mode") or "").upper() == "MANUAL_HISTORY":
                return _paper_write(
                    idempotency_key, "paper.order.draft.buy.historical", body,
                    lambda: create_historical_buy_draft(
                        _DB,
                        ts_code=body["ts_code"],
                        execution_trade_date=body["execution_trade_date"],
                        qty=int(body["qty"]),
                    ),
                )
            return _paper_write(
                idempotency_key, "paper.order.draft.buy", body,
                lambda: create_buy_draft(
                    _DB, ts_code=body["ts_code"], trade_date=body.get("trade_date")
                    or datetime.now().strftime("%Y%m%d"),
                    suggested_pos_pct=body.get("suggested_pos_pct"),
                    input_hash=body.get("input_hash") or "",
                    qty=body.get("qty"),
                ),
            )
        return _paper_write(
            idempotency_key, "paper.order.draft.sell", body,
            lambda: create_sell_draft(_DB, ts_code=body["ts_code"], qty=int(body["qty"])),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/orders/{order_id}/confirm")
def paper_confirm_order(
    order_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """确认订单：预交易检查 + 预留资产。"""
    from paper_trading.orders import confirm_order

    try:
        return _paper_write(
            idempotency_key, "paper.order.confirm", {"order_id": order_id},
            lambda: confirm_order(_DB, order_id),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/orders/{order_id}/cancel")
def paper_cancel_order(
    order_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """取消订单：释放预留。"""
    from paper_trading.orders import cancel_order

    try:
        return _paper_write(
            idempotency_key, "paper.order.cancel", {"order_id": order_id},
            lambda: cancel_order(_DB, order_id),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.get("/api/paper/positions")
def paper_positions():
    """持仓汇总。"""
    from paper_trading.settlement import get_positions

    try:
        return {"positions": get_positions(_DB)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/cycles/run")
def paper_run_cycle(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """手动补跑日结：{trade_date}。幂等（同日期已 DONE 返回原结果）。"""
    from paper_trading.settlement import run_settlement

    try:
        trade_date = body.get("trade_date") or datetime.now().strftime("%Y%m%d")
        return _paper_write(
            idempotency_key, "paper.cycle.run", body,
            lambda: run_settlement(_DB, trade_date),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.get("/api/paper/cycles/{trade_date}")
def paper_cycle_status(trade_date: str):
    """查看日结状态。"""
    from ab_screener.data.paper_query import cycle_status

    try:
        return cycle_status(_DB, trade_date)
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/reconciliation/run")
def paper_run_reconciliation(
    body: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """独立重跑对账。"""
    from paper_trading.settlement import run_reconciliation

    try:
        trade_date = body.get("trade_date") or datetime.now().strftime("%Y%m%d")
        return _paper_write(
            idempotency_key, "paper.reconciliation.run", body,
            lambda: run_reconciliation(_DB, trade_date),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.post("/api/paper/corporate-actions/{action_id}/apply")
def paper_apply_corporate_action(
    action_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from paper_trading.settlement import apply_corporate_action

    try:
        payload = {"action_id": action_id}
        return _paper_write(
            idempotency_key, "paper.corporate_action.apply", payload,
            lambda: apply_corporate_action(_DB, action_id),
        )
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.get("/api/paper/reconciliation")
def paper_reconciliation(trade_date: str | None = None):
    """查询对账记录及差异。"""
    from ab_screener.data.paper_query import list_reconciliations

    try:
        return {"items": list_reconciliations(_DB, trade_date)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.get("/api/paper/corporate-actions")
def paper_corporate_actions(status: str | None = None, limit: int = 50):
    from ab_screener.data.paper_query import list_corporate_actions

    try:
        return {"items": list_corporate_actions(_DB, status=status, limit=limit)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)


@router.get("/api/paper/fills")
def paper_fills(limit: int = 50):
    """查询成交记录。"""
    from ab_screener.data.paper_query import list_fills

    try:
        return {"fills": list_fills(_DB, limit=limit)}
    except Exception as e:  # noqa: BLE001
        _paper_err(e)










