"""App factory：挂载 v2 routers（与 legacy backend_app 并存）。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ab_screener.api.routers.ai_insight import router as ai_insight_router
from ab_screener.api.routers.desk import router as desk_router
from ab_screener.api.routers.intelligence import router as intelligence_router
from ab_screener.api.routers.monitor import router as monitor_router
from ab_screener.api.routers.paper import router as paper_router
from ab_screener.api.routers.readiness import router as readiness_router
from ab_screener.api.routers.research import router as research_router
from ab_screener.api.routers.review import router as review_router
from ab_screener.api.routers.risk import router as risk_router
from ab_screener.api.routers.scan_profiles import router as scan_profiles_router
from ab_screener.api.routers.signals import router as signals_router
from ab_screener.api.routers.strategies import router as strategies_router
from ab_screener.api.routers.system import router as system_router
from ab_screener.api.scan_router import router as scan_router
from ab_screener.application.platform_config import (
    flag_enabled,
    load_resolved_config,
    required_flags_for_path,
)
from ab_screener.domain.errors_v2 import V2Error
from build_version import build_version

V2_ROUTERS = (
    ai_insight_router,
    desk_router,
    intelligence_router,
    monitor_router,
    paper_router,
    readiness_router,
    research_router,
    review_router,
    risk_router,
    scan_profiles_router,
    signals_router,
    strategies_router,
    system_router,
)


def _install_platform_governance(app: FastAPI) -> None:
    """Resolve flags once and enforce them for every assembled v2 app."""
    if getattr(app.state, "platform_governance_installed", False):
        return
    app.state.platform_config = load_resolved_config()
    app.state.build_version = build_version()
    app.state.platform_governance_installed = True

    @app.middleware("http")
    async def platform_feature_gate(request: Request, call_next):
        resolved = request.app.state.platform_config
        for required_flag in required_flags_for_path(request.url.path):
            if not flag_enabled(resolved, required_flag):
                error = V2Error(
                    "FEATURE_DISABLED",
                    message="该功能尚未通过服务端开关启用",
                    details={"required_flag": required_flag},
                    http_status=503,
                )
                return JSONResponse(
                    status_code=503,
                    content={"detail": error.to_envelope()},
                    headers={
                        "X-AB-Version": str(request.app.state.build_version),
                        "X-AB-Product": "accumulation_breakout",
                    },
                )
        response = await call_next(request)
        response.headers["X-AB-Version"] = str(request.app.state.build_version)
        response.headers["X-AB-Product"] = "accumulation_breakout"
        return response


def include_v2_routers(app: FastAPI, *, include_scan_router: bool = True) -> None:
    """向现有 app 注册 v2 路由（独立 router；重复 path 由 OpenAPI 测试断言为 0）。

    include_scan_router=False：当宿主 app 已自带 legacy /api/scan 时跳过
    scan_router（backend_app 装配时使用），避免重复 Operation ID。
    """
    _install_platform_governance(app)
    if include_scan_router:
        app.include_router(scan_router)
    for router in V2_ROUTERS:
        app.include_router(router)
