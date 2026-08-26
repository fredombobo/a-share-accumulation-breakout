"""Astock 情报桥：supplement API 测试（TestClient GET，只读 + 契约字段）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ab_screener.api.app_factory import include_v2_routers


def _make_app() -> object:
    from fastapi import FastAPI

    app = FastAPI()
    include_v2_routers(app)
    return app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "supp.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE daily (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,"
            " close REAL, pre_close REAL, PRIMARY KEY (ts_code, trade_date))"
        )
        conn.executemany(
            "INSERT INTO daily (ts_code, trade_date, close, pre_close) VALUES (?,?,?,?)",
            [
                ("000001.SZ", "20260810", 11.0, 10.0),
                ("000002.SZ", "20260810", 9.8, 10.0),
                ("000001.SH", "20260810", 3100.0, 3090.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("AB_DB_PATH", str(db))
    monkeypatch.delenv("ASTOCK_BASE_URL", raising=False)
    return TestClient(_make_app())


def test_desk_supplement_get_ok(client):
    r = client.get("/api/v2/intelligence/desk-supplement?trade_date=20260810")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["side_effects"] is False
    assert body["not_a_pool"] is True
    assert body["status"] == "PASS"
    assert "limit_up" in body
    assert "indices" in body
    assert "disclaimer" in body


def test_desk_supplement_default_trade_date(client):
    r = client.get("/api/v2/intelligence/desk-supplement")
    assert r.status_code == 200, r.text
    # 缺省 trade_date 用 latest_trade_date
    assert r.json()["trade_date"] == "20260810"


def test_desk_supplement_http_degrades_200(client):
    """G4：未设 ASTOCK_BASE_URL，API 仍 200，astock.reachable=false。"""
    r = client.get("/api/v2/intelligence/desk-supplement?trade_date=20260810")
    assert r.status_code == 200
    body = r.json()
    assert body["astock"]["enabled"] is False
    assert body["astock"]["reachable"] is False


def test_limit_up_split_endpoint(client):
    """可选拆分 GET（若实现）——仅断言存在性，不强制。"""
    r = client.get("/api/v2/intelligence/limit-up?trade_date=20260810")
    # 允许 200（实现）或 404（未实现拆分），二者皆合法
    assert r.status_code in (200, 404)


def test_indices_split_endpoint(client):
    r = client.get("/api/v2/intelligence/indices?trade_date=20260810")
    assert r.status_code in (200, 404)
