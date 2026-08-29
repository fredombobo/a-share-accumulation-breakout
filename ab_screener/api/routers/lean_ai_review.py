"""Lean AI evidence review endpoints published on accumulation_breakout/8001."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.ai.client import has_provider
from ab_screener.api.deps import get_db_path
from ab_screener.intelligence.ai_analysis import (
    AIInsightError,
    analyze_stock,
    get_a_pool_candidates,
    local_evidence_review,
)

router = APIRouter(prefix="/api/ai-review", tags=["ai-evidence-review"])


def _signal(db_path: str, ts_code: str) -> tuple[dict[str, Any] | None, str]:
    for candidate in get_a_pool_candidates(db_path, top_n=500):
        if candidate["ts_code"] == ts_code:
            return candidate.get("signal"), str(candidate.get("run_id") or "")
    return None, ""


@router.get("/{ts_code}")
def local_review(ts_code: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    """Read-only deterministic review; never invokes a model and never writes."""
    try:
        return local_evidence_review(db_path, ts_code)
    except AIInsightError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "STOCK_NOT_FOUND", "message": str(exc), "details": {}, "retryable": False},
        ) from exc


@router.post("/{ts_code}/generate")
def generate_review(
    ts_code: str,
    body: dict[str, Any],
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """Explicit external-model enhancement; the GET endpoint remains side-effect free."""
    provider = str(body.get("provider") or "deepseek").lower()
    if provider not in {"deepseek", "openai", "ollama"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNKNOWN_AI_PROVIDER", "message": "不支持的 AI 提供方", "details": {"provider": provider}, "retryable": False},
        )
    if not has_provider(provider):
        raise HTTPException(
            status_code=503,
            detail={"code": "AI_PROVIDER_NOT_CONFIGURED", "message": f"{provider} 未配置；本地证据评测仍可使用", "details": {}, "retryable": True},
        )
    code = str(ts_code).strip().upper()
    signal, run_id = _signal(db_path, code)
    result = analyze_stock(
        db_path,
        code,
        signal=signal,
        refresh=True,
        provider=provider,
        run_id=run_id,
    )
    if not result.get("available"):
        raise HTTPException(
            status_code=503,
            detail={"code": "AI_PROVIDER_FAILED", "message": str(result.get("reason") or "AI 调用失败"), "details": {}, "retryable": True},
        )
    return {"review": local_evidence_review(db_path, code), "generated": result}
