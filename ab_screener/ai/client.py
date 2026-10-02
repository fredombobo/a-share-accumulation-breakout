"""多模型 AI 客户端（移植自 astock backend/ai/client.py）。

与 astock 的差异：用 requests（项目已有依赖）替代 httpx，函数签名保持一致
（chat / chat_stream），未来可零成本回迁 astock 或接入其它 OpenAI 兼容端点。

配置优先级：非空显式环境变量 > 项目根 `.env` 的 AI 字段 > 默认值。
每次读取都会重新查看配置文件，不修改进程环境，也不探测模型服务。
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]  # ai → ab_screener → 项目根
ENV_PATH = ROOT / ".env"

_PROVIDERS = ("deepseek", "openai", "ollama")
_AI_FIELDS = frozenset({
    "AI_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OLLAMA_BASE_URL", "OLLAMA_MODEL",
})


def _load_env_file() -> dict[str, str]:
    """Read only recognized AI settings, without exporting file contents."""
    values: dict[str, str] = {}
    try:
        raw = ENV_PATH.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return values
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _AI_FIELDS:
            values[key] = value.strip().strip('"').strip("'").strip()
    return values


def _configuration() -> dict[str, str]:
    values = _load_env_file()
    for key in _AI_FIELDS:
        explicit = os.environ.get(key, "").strip()
        if explicit:
            values[key] = explicit
    return values


def _models(values: dict[str, str]) -> dict[str, dict[str, str]]:
    return {
        "deepseek": {
            "base_url": values.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            "api_key": values.get("DEEPSEEK_API_KEY", ""),
            "model": values.get("DEEPSEEK_MODEL") or "deepseek-chat",
        },
        "openai": {
            "base_url": values.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            "api_key": values.get("OPENAI_API_KEY", ""),
            "model": values.get("OPENAI_MODEL") or "gpt-4o",
        },
        "ollama": {
            "base_url": values.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1",
            "api_key": "ollama",
            "model": values.get("OLLAMA_MODEL") or "qwen2.5:7b",
        },
    }


def _provider_name(provider: str) -> str:
    return provider.strip().lower() if isinstance(provider, str) else ""


def _usable_setting(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = normalized.removeprefix("sk_").strip("<>[]{}")
    return bool(normalized) and not (
        normalized in {"none", "null", "undefined", "changeme", "change_me", "replace_me",
                       "placeholder", "api_key", "token", "your_key", "your_api_key", "..."}
        or normalized.startswith(("your_", "replace_", "enter_", "insert_",
                                  "填写", "请填写", "在这里", "在此", "请输入", "替换为", "填入"))
        or set(normalized) == {"x"}
    )


def _configured(provider: str, values: dict[str, str]) -> bool:
    if provider not in _PROVIDERS:
        return False
    if provider == "ollama":
        # The synthetic API key and default model do not establish local setup.
        return _usable_setting(values.get("OLLAMA_MODEL", ""))
    return _usable_setting(values.get(f"{provider.upper()}_API_KEY", ""))


def default_provider() -> str:
    """Honor an explicit provider, including unknown names, then detect config."""
    values = _configuration()
    explicit = _provider_name(values.get("AI_PROVIDER", ""))
    if explicit:
        return explicit
    return next((name for name in _PROVIDERS if _configured(name, values)), "deepseek")


def _configured_model(provider: str) -> dict[str, str] | None:
    values = _configuration()
    name = _provider_name(provider)
    return _models(values)[name] if _configured(name, values) else None


class AIModel:
    """支持的 AI 模型配置（与 astock 一致）。"""

    @staticmethod
    def deepseek() -> dict[str, str]:
        return _models(_configuration())["deepseek"]

    @staticmethod
    def openai() -> dict[str, str]:
        return _models(_configuration())["openai"]

    @staticmethod
    def ollama() -> dict[str, str]:
        return _models(_configuration())["ollama"]

    @classmethod
    def get_all(cls) -> dict[str, dict[str, str]]:
        return _models(_configuration())


class AIClientError(RuntimeError):
    """Safe provider failure: details never include credentials or response bodies."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _messages(prompt: str, system: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def chat_checked(
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2000,
    provider: str = "deepseek",
) -> str:
    """Single response, or an explicit failure safe to display to the user."""
    if _provider_name(provider) not in _PROVIDERS:
        raise AIClientError("UNKNOWN_AI_PROVIDER", "不支持所选 AI 服务，请检查服务名称。")
    cfg = _configured_model(provider)
    if cfg is None:
        raise AIClientError("AI_PROVIDER_NOT_CONFIGURED", "所选 AI 服务尚未配置，请填写密钥或本地模型名称。")
    api_key = cfg["api_key"]

    payload = {
        "model": cfg["model"],
        "messages": _messages(prompt, system),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(
            f"{cfg['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=60,
            allow_redirects=False,
        )
    except requests.Timeout:
        raise AIClientError("AI_TIMEOUT", "AI 服务请求超时，请稍后重试。") from None
    except requests.RequestException:
        raise AIClientError("AI_CONNECTION_FAILED", "无法连接 AI 服务，请检查服务地址和网络。") from None
    if resp.status_code in (401, 403):
        raise AIClientError("AI_AUTH_FAILED", "AI 服务认证失败，请检查密钥和访问权限。")
    if resp.status_code == 429:
        raise AIClientError("AI_RATE_LIMIT", "AI 服务额度或请求频率受限，请检查额度后重试。")
    if resp.status_code != 200:
        raise AIClientError("AI_HTTP_ERROR", f"AI 服务返回 HTTP {resp.status_code}，请稍后重试。")
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        raise AIClientError("AI_INVALID_RESPONSE", "AI 服务响应格式无效，请检查接口和模型兼容性。") from None
    if not isinstance(content, str):
        raise AIClientError("AI_INVALID_RESPONSE", "AI 服务响应格式无效，请检查接口和模型兼容性。")
    if not content.strip():
        raise AIClientError("AI_EMPTY_RESPONSE", "AI 服务未返回分析内容，请稍后重试。")
    return content.strip()


def chat(
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2000,
    provider: str = "deepseek",
) -> str:
    """Compatibility interface: failures return an empty response."""
    try:
        return chat_checked(prompt, system, temperature, max_tokens, provider)
    except AIClientError:
        return ""


def chat_stream(
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2000,
    provider: str = "deepseek",
) -> Callable[[], list[str]]:
    """流式对话（同步阻塞实现）：返回一个迭代器函数，按 SSE 分块产出文本。

    与 astock 的异步 generator 语义对齐：每次调用返回 chunk 列表；
    调用方可用 `for chunk in chat_stream(...)():` 消费。
    """
    def _iter() -> list[str]:
        cfg = _configured_model(provider)
        if cfg is None:
            return []
        api_key = cfg["api_key"]
        chunks: list[str] = []
        payload = {
            "model": cfg["model"],
            "messages": _messages(prompt, system),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        try:
            with requests.post(
                f"{cfg['base_url'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=60,
                stream=True,
                allow_redirects=False,
            ) as resp:
                if resp.status_code != 200:
                    return []
                for line in resp.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        if isinstance(delta.get("content"), str):
                            chunks.append(delta["content"])
                    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                        continue
        except requests.RequestException:
            return []
        return chunks

    return _iter


def has_provider(provider: str = "deepseek") -> bool:
    """Whether local configuration is present; this does not probe connectivity."""
    return _configured(_provider_name(provider), _configuration())
