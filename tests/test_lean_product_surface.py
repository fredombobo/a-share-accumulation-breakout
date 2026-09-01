"""8001 publishes two product workflows, stock detail, AI review and help."""
from __future__ import annotations

import re
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
        "/api/classifications",
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
    assert '<Route path="/guide"' in source
    for removed in ('path="/lab"', 'path="/paper"'):
        assert removed not in source

    # 龙虎榜页面与 8001 无关：它们只由 8123 隔离产品（scripts/serve_lhb_product.py）
    # 提供服务，但与 8001 共用同一份 dist，所以路由必须留在 App.tsx 里。
    # 8001 的导航和 API 都不含龙虎榜——由本文件另外两个用例分别把守。
    v2_routes = re.findall(r'path="(/v2/[^"]*)"', source)
    assert v2_routes, "龙虎榜路由不应被整体删除，否则 8123 产品界面会白屏"
    assert all(route.startswith("/v2/lhb/") for route in v2_routes), v2_routes


def test_sidebar_has_two_product_entries_and_one_help_entry() -> None:
    source = (ROOT / "web" / "frontend" / "src" / "layout" / "Sidebar.tsx").read_text(
        encoding="utf-8"
    )
    assert "每日选股" in source
    assert "研究回测" in source
    assert "使用说明" in source
    for removed in ("纸面仿真", "策略实验室", "V2 控制台", "六形态"):
        assert removed not in source
