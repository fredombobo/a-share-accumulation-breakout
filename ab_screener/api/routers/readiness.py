"""Always-readable platform identity and seven-gate readiness endpoints."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from ab_screener.api.deps import get_db_path
from ab_screener.application.platform_config import HARD_GATES, load_resolved_config
from ab_screener.application.readiness_service import build_readiness_snapshot
from ab_screener.domain.readiness import GATES, ReadinessInput, evaluate_readiness

router = APIRouter(prefix="/api/v2", tags=["platform"])
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _resolved(request: Request) -> dict[str, Any]:
    configured = getattr(request.app.state, "platform_config", None)
    return configured if isinstance(configured, dict) else load_resolved_config()


def _safe_snapshot(
    request: Request,
    db_path: str,
) -> dict[str, Any]:
    try:
        return build_readiness_snapshot(_PROJECT_ROOT, db_path, _resolved(request))
    except Exception as exc:  # noqa: BLE001 - status endpoint must remain readable
        from tushare_init import sanitize_error

        verdict = evaluate_readiness(
            ReadinessInput(
                gate_results={gate: False for gate in GATES},
                worktree_clean=False,
                identity_matches=False,
            )
        )
        return {
            **verdict,
            "gates": {
                gate: {
                    "gate": gate,
                    "status": "INSUFFICIENT",
                    "passed": False,
                    "source": "collector",
                    "reason": "服务端证据采集失败",
                }
                for gate in GATES
            },
            "identity": {},
            "collection_error": sanitize_error(exc)[:240],
            "live_trading_enabled": False,
        }


@router.get("/readiness")
def readiness(
    request: Request,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """Read current server evidence; no client-supplied gate booleans exist."""
    return _safe_snapshot(request, db_path)


@router.get("/platform/status")
def platform_status(
    request: Request,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """Resolved flags/build/product identity; always readable and LIVE=false."""
    config = _resolved(request)
    snapshot = _safe_snapshot(request, db_path)
    build = str(getattr(request.app.state, "build_version", "") or "unknown")
    return {
        "status": "ok",
        "product": "accumulation_breakout",
        "display_name": "AB-Screener · 横盘吸筹突破",
        "default_port": 8001,
        "flags": dict(config.get("flags") or {}),
        "config_hash": config.get("resolved_hash"),
        "config_source": config.get("source"),
        "build_version": build,
        "live": False,
        "live_trading_enabled": False,
        "hard_gates": {gate: True for gate in HARD_GATES},
        "readiness": snapshot["status"],
        "readiness_detail": {
            "blocked_gates": snapshot.get("blocked_gates") or [],
            "identity_blockers": snapshot.get("identity_blockers") or [],
            "per_gate": snapshot.get("per_gate") or {},
        },
    }
