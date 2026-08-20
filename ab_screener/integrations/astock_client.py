"""可选 astock HTTP 客户端。失败必须降级，不得影响 AB 写路径。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT_SEC = 2.0


def astock_base_url(env: dict[str, str] | None = None) -> str:
    raw = (env or os.environ).get("ASTOCK_BASE_URL", "")
    return str(raw or "").strip().rstrip("/")


def fetch_json(url: str, *, timeout: float = DEFAULT_TIMEOUT_SEC) -> tuple[dict | list | None, str | None]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw), None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        return None, str(exc)[:200]


def probe_astock(base_url: str | None = None, *, timeout: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    """只读探测。未配置 URL → enabled=false。"""
    base = (base_url if base_url is not None else astock_base_url()).strip()
    if not base:
        return {
            "enabled": False,
            "reachable": False,
            "base_url": "",
            "global": None,
            "error": None,
        }
    health, err = fetch_json(f"{base}/health", timeout=timeout)
    if err or not isinstance(health, dict):
        return {
            "enabled": True,
            "reachable": False,
            "base_url": base,
            "global": None,
            "error": err or "invalid_health",
        }
    global_payload, gerr = fetch_json(f"{base}/api/market/global", timeout=timeout)
    return {
        "enabled": True,
        "reachable": True,
        "base_url": base,
        "service": health.get("service"),
        "global": global_payload if isinstance(global_payload, list) else None,
        "error": gerr,
    }
