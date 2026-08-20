"""指挥舱 API（P7.1）：今日唯一动作 + 全局摘要（只读，side_effects=false）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ab_screener.api.deps import get_db_path
from ab_screener.application.today_guide import build_today_guide

router = APIRouter(prefix="/api/v2/desk", tags=["desk"])


@router.get("")
def desk(db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    """今日唯一动作（服务端推导）与全局摘要。

    next_action 只允许 _COPY 中注册的动作；不携带任何写语义。
    """
    guide = build_today_guide(db_path)
    return {"side_effects": False, **guide}
