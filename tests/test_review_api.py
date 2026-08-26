"""P7.4 Review API 测试：notes/decisions 写读 + 未迁移 fail-closed。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ab_screener.api.app_factory import include_v2_routers
from ab_screener.data.migration_registry import apply_pending
from local_store import LocalStore


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "api.db"
    LocalStore(db_path=db)
    conn = sqlite3.connect(str(db))
    apply_pending(conn)
    conn.close()
    monkeypatch.setenv("AB_DB_PATH", str(db))
    from fastapi import FastAPI

    app = FastAPI()
    include_v2_routers(app)
    return TestClient(app)


def test_post_and_get_note(client):
    r = client.post("/api/v2/review/notes", json={
        "title": "想法", "body": "内容", "ref_type": "signal", "ref_id": "s-1",
    })
    assert r.status_code == 200, r.text
    note = r.json()
    assert note["title"] == "想法"
    assert note["ref_type"] == "signal"

    r2 = client.get("/api/v2/review/notes", params={"ref_type": "signal"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["count"] == 1
    assert body["items"][0]["note_id"] == note["note_id"]


def test_post_note_validation(client):
    r = client.post("/api/v2/review/notes", json={"title": ""})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "VALIDATION_FAILED"


def test_post_and_get_decision(client):
    r = client.post("/api/v2/review/decisions", json={
        "action": "PROMOTE_CANDIDATE", "rationale": "满足 robust 口径",
        "ref_type": "candidate", "ref_id": "c-1",
    })
    assert r.status_code == 200
    decision = r.json()
    assert decision["action"] == "PROMOTE_CANDIDATE"

    r2 = client.get("/api/v2/review/decisions", params={"ref_type": "candidate"})
    assert r2.status_code == 200
    assert r2.json()["count"] == 1


def test_weekly_endpoint(client):
    client.post("/api/v2/review/notes", json={"title": "周报"})
    r = client.get("/api/v2/review/weekly")
    assert r.status_code == 200
    assert r.json()["note_count"] == 1


def test_attribution_endpoint_uses_explicit_window_and_empty_store(client):
    r = client.get(
        "/api/v2/review/attribution",
        params={"start": "20260101", "end": "20260131"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "side_effects": False,
        "window": {"start": "20260101", "end": "20260131"},
        "count": 0,
        "message": "无归因事件",
    }


def test_unmigrated_db_fail_closed(tmp_path, monkeypatch):
    """未迁移的 DB：review 端点必须 404（不静默降级）。"""
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    monkeypatch.setenv("AB_DB_PATH", str(db))
    from fastapi import FastAPI

    app = FastAPI()
    include_v2_routers(app)
    c = TestClient(app)
    r = c.get("/api/v2/review/notes")
    assert r.status_code == 404
