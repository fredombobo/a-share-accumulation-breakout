"""Lean stock search must query the full local catalog without changing it."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ab_screener.api.deps import get_db_path
from ab_screener.api.routers.legacy_market import router


@pytest.fixture
def search_client(tmp_path):
    database = tmp_path / "catalog.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT, "
            "industry TEXT, list_date TEXT)"
        )
        connection.executemany(
            "INSERT INTO stock_basic VALUES (?, ?, ?, ?)",
            [(f"{index:06d}.SZ", f"样本{index}", "测试行业", "20000101") for index in range(1600)]
            + [("600176.SH", "中国巨石", "玻璃", "19990422")],
        )
        first_page = connection.execute(
            "SELECT ts_code FROM stock_basic ORDER BY ts_code LIMIT 1500"
        ).fetchall()
        assert ("600176.SH",) not in first_page
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_path] = lambda: str(database)
    with TestClient(app) as client:
        yield client, database


@pytest.mark.parametrize("query", ["600176", "中国巨石", "  中国巨石  "])
def test_search_finds_stock_beyond_legacy_first_1500_without_writes(search_client, query):
    client, database = search_client
    before = hashlib.sha256(database.read_bytes()).digest()
    response = client.get("/api/stock-search", params={"q": query})
    assert response.status_code == 200
    assert response.json() == [{
        "ts_code": "600176.SH", "name": "中国巨石", "industry": "玻璃", "list_date": "19990422",
    }]
    assert hashlib.sha256(database.read_bytes()).digest() == before


@pytest.mark.parametrize("query", [None, "", "   "])
def test_empty_query_does_not_query_catalog(search_client, monkeypatch, query):
    from ab_screener.intelligence import catalog

    def unexpected_query(*args, **kwargs):
        raise AssertionError("An empty search must not access the catalog")

    monkeypatch.setattr(catalog, "search_stocks", unexpected_query)
    client, _ = search_client
    response = client.get("/api/stock-search", params={} if query is None else {"q": query})
    assert response.status_code == 200
    assert response.json() == []


def test_search_query_length_is_bounded(search_client):
    client, _ = search_client
    assert client.get("/api/stock-search", params={"q": "x" * 80}).status_code == 200
    response = client.get("/api/stock-search", params={"q": "x" * 81})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "q"]
