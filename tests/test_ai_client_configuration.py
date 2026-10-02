"""AI configuration and safe failures; synthetic files and mocked HTTP only."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import requests

from ab_screener.ai import client


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path: Path, monkeypatch):
    path = tmp_path / "ai-settings.env"
    monkeypatch.setattr(client, "ENV_PATH", path)
    for key in client._AI_FIELDS:
        monkeypatch.delenv(key, raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("HTTP must be explicitly mocked in this test")

    monkeypatch.setattr(client.requests, "post", no_network)
    return path


class _Response:
    def __init__(self, status=200, payload=None, lines=()):
        self.status_code = status
        self.payload = payload
        self.lines = lines
        self.text = "sensitive-response-body"

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self, **kwargs):
        yield from self.lines


def _configured_openai(path):
    path.write_text("OPENAI_API_KEY=sk-live-example-key\n", encoding="utf-8")


def test_missing_configuration_does_not_claim_ollama_or_send_requests():
    assert client.default_provider() == "deepseek"
    for provider in ("deepseek", "openai", "ollama", "unknown"):
        assert client.has_provider(provider) is False
        assert client.chat("hello", provider=provider) == ""
        assert client.chat_stream("hello", provider=provider)() == []
    configs = client.AIModel.get_all()
    assert set(configs) == {"deepseek", "openai", "ollama"}
    assert all(set(config) == {"base_url", "api_key", "model"} for config in configs.values())
    assert configs["openai"]["base_url"] == "https://api.openai.com/v1"
    assert configs["openai"]["model"] == "gpt-4o"


@pytest.mark.parametrize("provider", ["deepseek", "openai"])
@pytest.mark.parametrize("key", ["", " ", "your_api_key_here", "YOUR_DEEPSEEK_API_KEY", "<your_key>",
                                 "sk-xxx", "changeme", "replace-me", "null", "填写实际密钥",
                                 "填写该服务的实际密钥", "请填写你的密钥", "在这里填写密钥"])
def test_placeholder_keys_are_not_configured(isolated_configuration, provider, key):
    isolated_configuration.write_text(f"{provider.upper()}_API_KEY={key}\n", encoding="utf-8")
    assert client.has_provider(provider) is False
    assert client.chat("hello", provider=provider) == ""


@pytest.mark.parametrize(("settings", "expected"), [
    ("OPENAI_API_KEY=usable-key\n", "openai"),
    ("OLLAMA_MODEL=qwen3:8b\n", "ollama"),
    ("OPENAI_API_KEY=usable-key\nOLLAMA_MODEL=qwen3:8b\n", "openai"),
    ("DEEPSEEK_API_KEY=usable-key\nOPENAI_API_KEY=usable-key\n", "deepseek"),
    ("DEEPSEEK_API_KEY=your_api_key\nOPENAI_API_KEY=usable-key\n", "openai"),
    ("AI_PROVIDER= OLLAMA \nDEEPSEEK_API_KEY=usable-key\n", "ollama"),
    ("AI_PROVIDER= Not-Installed \nDEEPSEEK_API_KEY=usable-key\n", "not-installed"),
])
def test_default_selection_honors_explicit_choice(isolated_configuration, settings, expected):
    isolated_configuration.write_text(settings, encoding="utf-8")
    assert client.default_provider() == expected


def test_file_hot_updates_and_ai_only_values_do_not_mutate_environment(isolated_configuration, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "existing-unrelated-setting")
    monkeypatch.delenv("UNRELATED_AI_TEST_VALUE", raising=False)
    isolated_configuration.write_text(
        "OPENAI_API_KEY=key-one\nOPENAI_MODEL=model-one\nTUSHARE_TOKEN=do-not-export\n"
        "UNRELATED_AI_TEST_VALUE=do-not-export\n", encoding="utf-8",
    )
    assert client.AIModel.openai()["model"] == "model-one"
    assert client.has_provider("openai") is True
    assert "OPENAI_API_KEY" not in os.environ
    assert "OPENAI_MODEL" not in os.environ
    assert os.environ["TUSHARE_TOKEN"] == "existing-unrelated-setting"
    assert "UNRELATED_AI_TEST_VALUE" not in os.environ
    # Same-length replacement cannot accidentally rely on file size as a cache key.
    isolated_configuration.write_text("OPENAI_API_KEY=key-two\nOPENAI_MODEL=model-two\n", encoding="utf-8")
    assert client.AIModel.openai()["api_key"] == "key-two"
    assert client.AIModel.openai()["model"] == "model-two"
    isolated_configuration.unlink()
    assert client.has_provider("openai") is False


def test_nonempty_environment_overrides_file_but_blank_environment_does_not(isolated_configuration, monkeypatch):
    isolated_configuration.write_text(
        "OPENAI_API_KEY=file-key\nOPENAI_MODEL=file-model\nAI_PROVIDER=openai\n", encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "  explicit-key  ")
    monkeypatch.setenv("OPENAI_MODEL", "   ")
    monkeypatch.setenv("AI_PROVIDER", "  unexpected  ")
    assert client.AIModel.openai()["api_key"] == "explicit-key"
    assert client.AIModel.openai()["model"] == "file-model"
    assert client.default_provider() == "unexpected"
    monkeypatch.setenv("AI_PROVIDER", " ")
    assert client.default_provider() == "openai"


def test_ollama_requires_explicit_model_even_with_url(isolated_configuration):
    isolated_configuration.write_text("OLLAMA_BASE_URL=http://127.0.0.1:9988/v1/\nOLLAMA_MODEL=  \n", encoding="utf-8")
    assert client.has_provider("ollama") is False
    isolated_configuration.write_text("OLLAMA_MODEL=qwen3:8b\n", encoding="utf-8")
    assert client.has_provider("ollama") is True
    assert client.AIModel.ollama()["base_url"] == "http://localhost:11434/v1"


@pytest.mark.parametrize("model", ["填写本机已安装的模型标识", "请填写模型名称", "在这里填入模型", "your_model"])
def test_ollama_placeholder_model_is_not_configured(isolated_configuration, model):
    isolated_configuration.write_text(f"OLLAMA_MODEL={model}\n", encoding="utf-8")
    assert client.has_provider("ollama") is False
    assert client.default_provider() == "deepseek"
    assert client.chat("hello", provider="ollama") == ""


def test_custom_openai_endpoint_model_and_checked_chat(isolated_configuration, monkeypatch):
    isolated_configuration.write_text(
        "OPENAI_API_KEY=custom-key\nOPENAI_BASE_URL=https://gateway.example/v1///\n"
        "OPENAI_MODEL=custom-model\n", encoding="utf-8",
    )
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(payload={"choices": [{"message": {"content": "  result  "}}]})

    monkeypatch.setattr(client.requests, "post", post)
    assert client.chat_checked("question", "context", 0.2, 100, "openai") == "result"
    assert calls[0][0] == "https://gateway.example/v1/chat/completions"
    kwargs = calls[0][1]
    assert kwargs["json"] == {"model": "custom-model", "messages": [
        {"role": "system", "content": "context"}, {"role": "user", "content": "question"},
    ], "temperature": 0.2, "max_tokens": 100}
    assert kwargs["headers"] == {"Authorization": "Bearer custom-key"}
    assert kwargs["timeout"] == 60 and kwargs["allow_redirects"] is False


def test_unknown_provider_does_not_fallback_to_configured_deepseek(isolated_configuration):
    isolated_configuration.write_text("DEEPSEEK_API_KEY=usable-key\nAI_PROVIDER=unknown\n", encoding="utf-8")
    assert client.default_provider() == "unknown"
    assert client.has_provider("unknown") is False
    with pytest.raises(client.AIClientError) as error:
        client.chat_checked("prompt", provider="unknown")
    assert error.value.code == "UNKNOWN_AI_PROVIDER"
    assert client.chat("prompt", provider="unknown") == ""
    assert client.chat_stream("prompt", provider="unknown")() == []


def test_unconfigured_checked_chat_is_structured():
    with pytest.raises(client.AIClientError) as error:
        client.chat_checked("prompt", provider="openai")
    assert error.value.code == "AI_PROVIDER_NOT_CONFIGURED"


@pytest.mark.parametrize(("status", "code"), [
    (401, "AI_AUTH_FAILED"), (403, "AI_AUTH_FAILED"), (429, "AI_RATE_LIMIT"),
    (302, "AI_HTTP_ERROR"), (500, "AI_HTTP_ERROR"),
])
def test_http_errors_do_not_expose_body_or_credentials(isolated_configuration, monkeypatch, status, code):
    _configured_openai(isolated_configuration)
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: _Response(status=status))
    with pytest.raises(client.AIClientError) as error:
        client.chat_checked("prompt", provider="openai")
    assert error.value.code == code
    assert "sensitive-response-body" not in error.value.message
    assert "sk-live-example-key" not in str(error.value)
    assert client.chat("prompt", provider="openai") == ""


@pytest.mark.parametrize(("exception", "code"), [
    (requests.Timeout("private-request-details"), "AI_TIMEOUT"),
    (requests.ConnectionError("private-request-details"), "AI_CONNECTION_FAILED"),
])
def test_transport_errors_are_safe(isolated_configuration, monkeypatch, exception, code):
    _configured_openai(isolated_configuration)

    def failing(*args, **kwargs):
        raise exception

    monkeypatch.setattr(client.requests, "post", failing)
    with pytest.raises(client.AIClientError) as error:
        client.chat_checked("prompt", provider="openai")
    assert error.value.code == code
    assert "private-request-details" not in str(error.value)
    assert error.value.__suppress_context__ is True
    assert client.chat("prompt", provider="openai") == ""


@pytest.mark.parametrize("payload", [None, {}, [], {"choices": []},
    {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": ["not text"]}}]}, ValueError("private-json-body")])
def test_invalid_response_is_structured(isolated_configuration, monkeypatch, payload):
    _configured_openai(isolated_configuration)
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: _Response(payload=payload))
    with pytest.raises(client.AIClientError) as error:
        client.chat_checked("prompt", provider="openai")
    assert error.value.code == "AI_INVALID_RESPONSE"
    assert "private-json-body" not in str(error.value)
    assert client.chat("prompt", provider="openai") == ""


def test_empty_text_response_is_explicit(isolated_configuration, monkeypatch):
    _configured_openai(isolated_configuration)
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: _Response(
        payload={"choices": [{"message": {"content": " \n "}}]},
    ))
    with pytest.raises(client.AIClientError) as error:
        client.chat_checked("prompt", provider="openai")
    assert error.value.code == "AI_EMPTY_RESPONSE"
    assert client.chat("prompt", provider="openai") == ""


def test_stream_reads_current_settings_and_does_not_follow_redirects(isolated_configuration, monkeypatch):
    stream = client.chat_stream("prompt", provider="ollama")
    isolated_configuration.write_text(
        "OLLAMA_MODEL=local-model\nOLLAMA_BASE_URL=http://127.0.0.1:9988/v1///\n", encoding="utf-8",
    )
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(lines=[
            "data: " + json.dumps({"choices": [{"delta": {"content": None}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": "piece"}}]}),
            "data: [DONE]",
        ])

    monkeypatch.setattr(client.requests, "post", post)
    assert stream() == ["piece"]
    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:9988/v1/chat/completions"
    assert kwargs["json"]["model"] == "local-model"
    assert kwargs["timeout"] == 60 and kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: _Response(status=302))
    assert stream() == []
