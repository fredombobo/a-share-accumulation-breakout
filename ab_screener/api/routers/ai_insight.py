"""AI 解读 API（P8.1）：A 池候选的 DeepSeek 五维评分解读（只读 + 缓存幂等）。

- GET /api/v2/intelligence/ai/pool         A 池 top_n 批量解读（默认读缓存）
- GET /api/v2/intelligence/ai/stocks/{ts_code}  单股解读
- GET /api/v2/intelligence/ai/insights     已缓存解读列表
- GET /api/v2/intelligence/ai/status       配置状态（是否已配置 Key）

架构契约：本层不得直接 import sqlite3/subprocess（见 scripts/check_architecture.py），
一律委托 `ab_screener.intelligence.ai_analysis`。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ab_screener.api.deps import get_db_path
from ab_screener.ai.client import has_provider
from ab_screener.intelligence.ai_analysis import (
    analyze_pool,
    analyze_stock,
    get_a_pool_candidates,
    list_insights,
)

router = APIRouter(prefix="/api/v2/intelligence/ai", tags=["intelligence-ai"])


@router.get("/status")
def status() -> dict[str, Any]:
    """AI 解读配置状态（前端据此决定是否展示入口）。"""
    return {
        "deepseek_configured": has_provider("deepseek"),
        "providers": {
            p: has_provider(p) for p in ("deepseek", "openai", "ollama")
        },
    }


@router.get("/pool")
def pool_analysis(
    db_path: str = Depends(get_db_path),
    top_n: int = Query(15, ge=5, le=30),
    refresh: bool = False,
) -> dict[str, Any]:
    """A 池 top_n 候选批量 AI 解读。refresh=true 时重新调用 LLM（消耗 token）。"""
    if not has_provider("deepseek"):
        raise HTTPException(
            status_code=503,
            detail="未配置 DeepSeek API Key（请在项目根 .env 设置 DEEPSEEK_API_KEY）",
        )
    return analyze_pool(db_path, top_n=top_n, refresh=refresh)


@router.get("/stocks/{ts_code}")
def stock_analysis(
    ts_code: str,
    db_path: str = Depends(get_db_path),
    refresh: bool = False,
) -> dict[str, Any]:
    """单股 AI 解读。默认按该股最新 A 池信号解读；无信号时做独立解读。"""
    signal = None
    for c in get_a_pool_candidates(db_path, top_n=30):
        if c["ts_code"] == ts_code:
            signal = c["signal"]
            break
    result = analyze_stock(db_path, ts_code, signal=signal, refresh=refresh, run_id="")
    if not result.get("available"):
        raise HTTPException(status_code=503, detail=result.get("reason", "LLM 调用失败"))
    return result


@router.get("/insights")
def cached_insights(
    db_path: str = Depends(get_db_path),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """已缓存的 AI 解读列表（最新在前）。"""
    items = list_insights(db_path, limit=limit)
    return {"items": items, "count": len(items)}
