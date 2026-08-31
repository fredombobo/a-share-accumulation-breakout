"""P7.1 OpenAPI 契约测试：v2 最小公共 API 存在性 + 重复 path = 0。"""
from __future__ import annotations

from fastapi import FastAPI

from ab_screener.api.app_factory import include_v2_routers

# v2 最小公共 API（计划 P7.1 表格子集：本阶段已交付）
REQUIRED_V2_PATHS = {
    "GET /api/v2/desk",
    "GET /api/v2/intelligence/search",
    "GET /api/v2/intelligence/stocks/{ts_code}",
    "GET /api/v2/intelligence/stocks/{ts_code}/timeline",
    "GET /api/v2/intelligence/breadth",
    "GET /api/v2/intelligence/data-status",
    "GET /api/v2/intelligence/desk-supplement",
    "GET /api/v2/intelligence/limit-up",
    "GET /api/v2/intelligence/indices",
    "GET /api/v2/strategies",
    "GET /api/v2/strategies/{strategy_id}/versions",
    "GET /api/v2/scan-profiles",
    "POST /api/v2/scan-profiles",
    "GET /api/v2/signals/observations/{observation_id}",
    "GET /api/v2/signals/observations/{observation_id}/outcomes",
    "GET /api/v2/portfolio/risk",
    "POST /api/v2/portfolio/stress",
    "GET /api/v2/research/experiments",
    "POST /api/v2/research/experiments",
    "POST /api/v2/research/experiments/{experiment_id}/runs",
    "GET /api/v2/research/runs/{run_id}",
    "POST /api/v2/research/runs/{run_id}/cancel",
    "GET /api/v2/paper/status",
    "GET /api/v2/review/notes",
    "POST /api/v2/review/notes",
    "GET /api/v2/review/decisions",
    "POST /api/v2/review/decisions",
    "GET /api/v2/review/weekly",
    "GET /api/v2/review/attribution",
    "GET /api/v2/alerts",
    "POST /api/v2/alerts/{alert_id}/read",
    "GET /api/v2/system/health",
    "GET /api/v2/system/backups",
    "GET /api/v2/system/audit",
    "GET /api/v2/lhb/radar",
    "GET /api/v2/lhb/events",
    "GET /api/v2/lhb/seats/{seat_id}",
    "GET /api/v2/lhb/actors/{actor_id}",
    "GET /api/v2/lhb/stocks/{ts_code}/timeline",
    "GET /api/v2/lhb/network",
    "GET /api/v2/lhb/quality",
    "GET /api/v2/lhb/signals",
    "GET /api/v2/lhb/backtest",
}


def _build_app() -> FastAPI:
    app = FastAPI()
    include_v2_routers(app)
    return app


def test_v2_minimal_api_present():
    app = _build_app()
    schema = app.openapi()
    paths = schema.get("paths", {})
    methods = set()
    for path, ops in paths.items():
        for method in ops:
            methods.add(f"{method.upper()} {path}")
    missing = REQUIRED_V2_PATHS - methods
    assert not missing, f"缺失 v2 路径: {missing}"


def test_no_duplicate_paths():
    app = _build_app()
    schema = app.openapi()
    paths = schema.get("paths", {})
    # 同一 (method, path) 只出现一次
    seen: set[tuple[str, str]] = set()
    duplicates = []
    for path, ops in paths.items():
        for method in ops:
            key = (method.upper(), path)
            if key in seen:
                duplicates.append(key)
            seen.add(key)
    assert not duplicates, f"重复 path: {duplicates}"


def test_v2_paths_have_tags():
    app = _build_app()
    schema = app.openapi()
    for path, ops in schema.get("paths", {}).items():
        if path.startswith("/api/v2/"):
            for method, op in ops.items():
                if method.lower() in ("get", "post"):
                    assert op.get("tags"), f"{method} {path} 缺少 tags"
