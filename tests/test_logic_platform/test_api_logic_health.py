"""/api/logic/health 挂载测试（TestClient 直接打宿主 app）。"""
from __future__ import annotations

from starlette.testclient import TestClient

from web.backend_app import app

client = TestClient(app)


def test_health_200_and_fields():
    resp = client.get("/api/logic/health")
    assert resp.status_code == 200
    body = resp.json()
    for key in ["enabled", "lake", "schema_version", "feature_version",
                "research_only", "as_of"]:
        assert key in body, f"缺字段 {key}"
    assert isinstance(body["lake"], dict)
    assert "ok" in body["lake"]


def test_features_unknown_code_graceful():
    resp = client.get("/api/logic/features/999999.NONE")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("error") == "no_data"
    assert body.get("research_only") is True


def test_explain_unknown_code_graceful():
    resp = client.get("/api/logic/explain/999999.NONE")
    assert resp.status_code == 200
    assert resp.json().get("error") == "no_data"


def test_predict_bad_request():
    resp = client.post("/api/logic/predict", json={})
    assert resp.status_code == 200
    assert resp.json().get("error") == "bad_request"


def test_predict_batch_ok_or_warning():
    """模型存在 → 返回 results；不存在 → warning（都不报错）。"""
    resp = client.post("/api/logic/predict", json={"ts_codes": ["000001.SZ"]})
    assert resp.status_code == 200
    body = resp.json()
    assert "model_version" in body or "warning" in body
    assert body.get("research_only") is True


def test_strategies_list_and_detail():
    resp = client.get("/api/logic/strategies")
    assert resp.status_code == 200
    d = resp.json()
    assert "strategies" in d and "count" in d
    if d["strategies"]:
        sid = d["strategies"][0]["id"]
        det = client.get(f"/api/logic/strategies/{sid}").json()
        assert det["strategy"]["id"] == sid
        assert "dsl_yaml" in det["strategy"]


def test_strategies_unknown_graceful():
    resp = client.get("/api/logic/strategies/no_such_strategy_xyz")
    assert resp.status_code == 200
    assert resp.json().get("error") == "not_found"


def test_backtest_detail_unknown_graceful():
    resp = client.get("/api/logic/backtest/no_such_run")
    assert resp.status_code == 200
    assert resp.json().get("error") == "not_found"
