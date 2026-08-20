"""多模型 AI 客户端（移植自 astock backend/ai/client.py）。

与 astock 的差异：用 requests（项目已有依赖）替代 httpx，函数签名保持一致
（chat / chat_stream），未来可零成本回迁 astock 或接入其它 OpenAI 兼容端点。

API Key 解析优先级：环境变量 `DEEPSEEK_API_KEY` > 项目根 `.env` 文件的
`DEEPSEEK_API_KEY=` 行（与 bootstrap.py 读取 TUSHARE_TOKEN 的方式一致，不依赖 dotenv）。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import requests

ROOT = Path(__file__).resolve().parents[2]  # ai → ab_screener → 项目根
ENV_PATH = ROOT / ".env"

_lock = threading.Lock()
_env_loaded = False


def _load_env_file() -> None:
    """一次性解析项目根 .env（仅取缺失的 key，不覆盖已存在的环境变量）。"""
    global _env_loaded
    with _lock:
        if _env_loaded:
            return
        _env_loaded = True
        try:
            raw = ENV_PATH.read_text(encoding="utf-8-sig")
        except OSError:
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _resolve(key: str, default: str = "") -> str:
    _load_env_file()
    return os.environ.get(key, default) or default


class AIModel:
    """支持的 AI 模型配置（与 astock 一致）。"""

    @staticmethod
    def deepseek() -> dict[str, str]:
        return {
            "base_url": _resolve("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "api_key": _resolve("DEEPSEEK_API_KEY"),
            "model": _resolve("DEEPSEEK_MODEL", "deepseek-chat"),
        }

    @staticmethod
    def openai() -> dict[str, str]:
        return {
            "base_url": "https://api.openai.com/v1",
            "api_key": _resolve("OPENAI_API_KEY"),
            "model": "gpt-4o",
        }

    @staticmethod
    def ollama() -> dict[str, str]:
        return {
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
            "model": _resolve("OLLAMA_MODEL", "qwen2.5:7b"),
        }

    @classmethod
    def get_all(cls) -> dict[str, dict[str, str]]:
        return {
            "deepseek": cls.deepseek(),
            "openai": cls.openai(),
            "ollama": cls.ollama(),
        }


def _messages(prompt: str, system: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def chat(
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2000,
    provider: str = "deepseek",
) -> str:
    """单次对话；失败返回空串（与 astock 行为一致，调用方自行降级）。"""
    cfg = AIModel.get_all().get(provider) or AIModel.deepseek()
    api_key = cfg.get("api_key")
    if not api_key:
        return ""

    payload = {
        "model": cfg["model"],
        "messages": _messages(prompt, system),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(
            f"{cfg['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=120,
        )
    except requests.RequestException:
        return ""
    if resp.status_code != 200:
        return ""
    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError):
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
    cfg = AIModel.get_all().get(provider) or AIModel.deepseek()
    api_key = cfg.get("api_key")

    def _iter() -> list[str]:
        if not api_key:
            return []
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
                f"{cfg['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=120,
                stream=True,
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
                        if "content" in delta:
                            chunks.append(delta["content"])
                    except (ValueError, KeyError, IndexError):
                        continue
        except requests.RequestException:
            return []
        return chunks

    return _iter


def has_provider(provider: str = "deepseek") -> bool:
    """是否已配置可用 API Key（供前端展示/路由降级判断）。"""
    cfg = AIModel.get_all().get(provider) or AIModel.deepseek()
    return bool(cfg.get("api_key"))
