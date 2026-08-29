"""8001 only publishes daily selection, detail, professional backtest and AI review."""
from __future__ import annotations

from pathlib import Path

from web.backend_app import app

ROOT = Path(__file__).resolve().parents[1]


def test_product_openapi_keeps_core_and_excludes_immature_surfaces() -> None:
    paths = set(app.openapi()["paths"])
    expected = {
        "/api/health",
        "/api/overview",
        "/api/scan",
        "/api/sync/status",
        "/api/backtest/catalog",
        "/api/ai-review/{ts_code}",
        "/api/v2/platform/status",
        "/api/v2/system/health",
    }
    assert expected <= paths

    removed_prefixes = (
        "/api/lab",
        "/api/paper",
        "/api/logic",
        "/api/v2/desk",
        "/api/v2/intelligence",
        "/api/v2/alerts",
        "/api/v2/research",
        "/api/v2/review",
        "/api/v2/portfolio",
        "/api/v2/scan-profiles",
        "/api/v2/signals",
        "/api/v2/strategies",
    )
    assert not [path for path in paths if path.startswith(removed_prefixes)]


def test_frontend_route_manifest_is_lean() -> None:
    source = (ROOT / "web" / "frontend" / "src" / "App.tsx").read_text(
        encoding="utf-8"
    )
    assert '<Route path="/"' in source
    assert '<Route path="/stock/:tsCode"' in source
    assert '<Route path="/backtest"' in source
    for removed in ('path="/lab"', 'path="/paper"', 'path="/v2/'):
        assert removed not in source


def test_sidebar_has_only_two_product_entries() -> None:
    source = (ROOT / "web" / "frontend" / "src" / "layout" / "Sidebar.tsx").read_text(
        encoding="utf-8"
    )
    assert "每日选股" in source
    assert "专业回测" in source
    for removed in ("纸面仿真", "策略实验室", "V2 控制台", "六形态"):
        assert removed not in source
