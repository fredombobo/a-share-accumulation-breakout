"""Astock 情报桥：HTTP 客户端测试（无 URL / mock 成功 / mock 抛错不 raise）。"""
from __future__ import annotations

import json
from typing import Any

from ab_screener.integrations.astock_client import (
    astock_base_url,
    fetch_json,
    probe_astock,
)


def test_astock_base_url_empty():
    assert astock_base_url({}) == ""
    assert astock_base_url({"ASTOCK_BASE_URL": "  "}) == ""


def test_astock_base_url_strips_slash():
    assert astock_base_url({"ASTOCK_BASE_URL": "http://127.0.0.1:8900/"}) == "http://127.0.0.1:8900"


def test_probe_no_url():
    r = probe_astock("")
    assert r["enabled"] is False
    assert r["reachable"] is False
    assert r["global"] is None
    assert r["error"] is None


def test_probe_health_ok_global(monkeypatch):
    """mock urlopen：health 200 + global 200 → reachable=True。"""
    class _Resp:
        def read(self) -> bytes:
            return json.dumps({"service": "A-Stock"}).encode()

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def fake_urlopen(req: Any, timeout: float):
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    # fetch_json 两次调用：health 返回 dict，global 返回 list
    payloads = [{"service": "A-Stock"}, [{"code": "000001.SH"}]]

    class _Resp2:
        def __init__(self, p: Any):
            self._p = p

        def read(self) -> bytes:
            return json.dumps(self._p).encode()

        def __enter__(self) -> _Resp2:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    calls = iter(payloads)

    def fake2(req: Any, timeout: float):
        return _Resp2(next(calls))

    monkeypatch.setattr("urllib.request.urlopen", fake2)
    r = probe_astock("http://127.0.0.1:8900", timeout=1.0)
    assert r["enabled"] is True
    assert r["reachable"] is True
    assert r["global"] == [{"code": "000001.SH"}]


def test_probe_health_error_degrades(monkeypatch):
    """mock urlopen 抛错 → 不 raise，reachable=False + error 短字符串。"""

    def boom(req: Any, timeout: float):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    r = probe_astock("http://127.0.0.1:8900", timeout=1.0)
    assert r["enabled"] is True
    assert r["reachable"] is False
    assert r["error"]


def test_fetch_json_invalid(monkeypatch):
    class _Bad:
        def read(self) -> bytes:
            return b"not-json"

        def __enter__(self) -> _Bad:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _Bad())
    data, err = fetch_json("http://x/health")
    assert data is None
    assert err
