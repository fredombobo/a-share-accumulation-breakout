"""本机访问守卫：Host / Origin 白名单与 TestClient 专用放行（不访问数据库）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from web import backend_app as backend

PROBE = "/api/__guard_probe_not_a_route__"


def test_testclient_default_host_is_allowed() -> None:
    client = TestClient(backend.app)
    assert client.get(PROBE).status_code == 404


def test_testserver_host_from_real_peer_is_rejected() -> None:
    # 真实套接字对端（IP）即便把 Host 伪装成 testserver 也必须拒绝
    client = TestClient(backend.app, client=("192.168.1.23", 50000))
    response = client.get(PROBE)
    assert response.status_code == 403


def test_local_host_from_real_peer_is_allowed() -> None:
    client = TestClient(backend.app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 50000))
    assert client.get(PROBE).status_code == 404


def test_rebinding_host_is_rejected() -> None:
    client = TestClient(backend.app, base_url="http://attacker.example:8001")
    assert client.get(PROBE).status_code == 403


def test_cross_site_write_is_rejected_and_cli_write_allowed() -> None:
    client = TestClient(backend.app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 50000))
    blocked = client.post(PROBE, headers={"Origin": "https://evil.example"})
    assert blocked.status_code == 403
    assert client.post(PROBE).status_code in (404, 405)
