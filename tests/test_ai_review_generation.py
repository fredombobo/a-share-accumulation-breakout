from __future__ import annotations

import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ab_screener.ai.client import AIClientError
from ab_screener.api.routers import lean_ai_review as route
from ab_screener.intelligence import ai_analysis as analysis


def _review():
    return {"ts_code": "000001.SZ", "signal_date": "20260911", "evidence": [], "risks": []}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[route.get_db_path] = lambda: "unused.db"
    monkeypatch.setattr(route, "local_evidence_review", lambda *_: copy.deepcopy(_review()))
    monkeypatch.setattr(route, "_signal", lambda *_: (None, ""))
    monkeypatch.setattr(route.AIModel, "get_all", lambda: {
        "deepseek": {"model": "deepseek-chat", "api_key": "", "base_url": "https://example.test"},
        "openai": {"model": "user-model", "api_key": "private-model-credential", "base_url": "https://gateway.test"},
        "ollama": {"model": "local-model", "api_key": "ollama", "base_url": "http://localhost:11434/v1"},
    })
    return TestClient(app)


def test_read_only_capability_uses_configured_provider_without_calling_model(client, monkeypatch):
    monkeypatch.setattr(route, "default_provider", lambda: "openai")
    monkeypatch.setattr(route, "has_provider", lambda provider: provider == "openai")
    monkeypatch.setattr(route, "analyze_stock", lambda *_a, **_k: pytest.fail("GET must not invoke a model"))
    data = client.get("/api/ai-review/000001.SZ").json()
    assert data["generation"]["provider"] == "openai"
    assert data["generation"]["available"] is True
    assert data["generation"]["status"] == "configured"
    assert data["generation"]["model"] == "user-model"
    assert "尚未验证" in data["generation"]["message"]
    assert "private-model-credential" not in json.dumps(data)
    assert "base_url" not in json.dumps(data)


def test_generate_defaults_to_selected_provider_and_does_not_write_production_cache(client, monkeypatch):
    monkeypatch.setattr(route, "default_provider", lambda: "openai")
    monkeypatch.setattr(route, "has_provider", lambda provider: provider == "openai")
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        return {"available": True, "provider": "openai", "model": "user-model", "ai_text": "证据不足"}

    monkeypatch.setattr(route, "analyze_stock", generate)
    response = client.post("/api/ai-review/000001.SZ/generate", json={})
    assert response.status_code == 200
    assert calls[0]["provider"] == "openai"
    assert calls[0]["persist"] is False
    assert calls[0]["refresh"] is True


@pytest.mark.parametrize("configured,provider,expected", [
    (False, "deepseek", "AI_PROVIDER_NOT_CONFIGURED"),
    (True, "unknown", "UNKNOWN_AI_PROVIDER"),
])
def test_invalid_configuration_never_generates(client, monkeypatch, configured, provider, expected):
    monkeypatch.setattr(route, "has_provider", lambda *_: configured)
    monkeypatch.setattr(route, "analyze_stock", lambda *_a, **_k: pytest.fail("must not call"))
    response = client.post("/api/ai-review/000001.SZ/generate", json={"provider": provider})
    assert response.json()["detail"]["code"] == expected


def test_provider_failure_preserves_specific_reason(client, monkeypatch):
    monkeypatch.setattr(route, "has_provider", lambda *_: True)
    monkeypatch.setattr(route, "analyze_stock", lambda *_a, **_k: {
        "available": False, "error_code": "AI_AUTH_FAILED", "reason": "模型认证失败，请检查 API Key。",
    })
    response = client.post("/api/ai-review/000001.SZ/generate", json={"provider": "openai"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AI_AUTH_FAILED"
    assert "DEEPSEEK" not in response.text


@pytest.mark.parametrize("fail", [False, True])
def test_ephemeral_generation_uses_evidence_date_and_never_creates_cache(monkeypatch, fail):
    monkeypatch.setattr(analysis, "build_stock_context", lambda *_: {
        "as_of": "20260911", "signal_context": "无信号", "stock_info": "公开行情",
        "kline_summary": "MA20 10", "financials": "缺失", "fund_flow": "缺失",
        "news": "缺失", "current_price": "10.00",
    })
    monkeypatch.setattr(analysis, "_save_insight", lambda *_a, **_k: pytest.fail("no production writes"))
    monkeypatch.setattr(analysis, "_insight_cache", lambda *_: pytest.fail("no stale cache reuse"))
    monkeypatch.setattr(analysis.AIModel, "get_all", lambda: {"openai": {"model": "model-for-test"}})

    def generate(*_a, **_k):
        if fail:
            raise AIClientError("AI_TIMEOUT", "模型响应超时。")
        return "证据不足"

    monkeypatch.setattr(analysis, "chat_checked", generate)
    result = analysis.analyze_stock("unused.db", "000001.SZ", provider="openai", persist=False)
    assert result["signal_date"] == "20260911"
    assert result["available"] is not fail
    if fail:
        assert result["error_code"] == "AI_TIMEOUT"
    else:
        assert result["model"] == "model-for-test"
        assert result["persisted"] is False
