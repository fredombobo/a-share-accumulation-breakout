"""纸面账户 API（P7.1）：只读摘要，不复制第二套 v2 账本接口。

契约：现有 `/api/paper/*` 兼容接口保持原样；本 router 仅提供
只读账户状态，供控制台摘要使用（side_effects=false）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from paper_trading.account import get_account
from paper_trading.errors import DomainError

router = APIRouter(prefix="/api/v2/paper", tags=["paper"])


@router.get("/status")
def paper_status(db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    """账户存在性与现金摘要（只读；未迁移/无账户 → 404 fail-closed）。"""
    try:
        account = get_account(db_path)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        msg = str(exc).lower()
        if "no such table" in msg or "no such column" in msg:
            raise HTTPException(status_code=404, detail=f"纸面表未迁移: {exc}") from exc
        raise
    return {
        "side_effects": False,
        "account_exists": True,
        "account_id": account.get("account_id"),
        "cash_fen": account.get("cash_fen"),
        "cash_cny": account.get("cash_cny") or (
            f"{int(account['cash_fen']) / 100:.2f}" if account.get("cash_fen") is not None else None
        ),
        "status": account.get("status"),
    }
