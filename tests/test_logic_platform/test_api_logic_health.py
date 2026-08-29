"""精简产品不向 8001 暴露 research-only logic platform。"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from web.backend_app import app

client = TestClient(app)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/logic/health", None),
        ("GET", "/api/logic/features/999999.NONE", None),
        ("GET", "/api/logic/explain/999999.NONE", None),
        ("POST", "/api/logic/predict", {}),
        ("POST", "/api/logic/predict", {"ts_codes": ["000001.SZ"]}),
        ("GET", "/api/logic/strategies", None),
        ("GET", "/api/logic/strategies/no_such_strategy_xyz", None),
        ("GET", "/api/logic/backtest/no_such_run", None),
    ],
)
def test_logic_platform_is_not_mounted_on_product(
    method: str, path: str, payload: dict[str, object] | None
) -> None:
    response = client.request(method, path, json=payload)
    # GET 未命中返回 404；POST 会命中 SPA 的 GET 回退路径并返回 405。
    assert response.status_code in {404, 405}
