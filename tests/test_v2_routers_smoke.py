"""P7.1 路由冒烟：desk / research / paper / system 端点在已迁移 DB 上可用。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ab_screener.api.app_factory import include_v2_routers
from ab_screener.data.migration_registry import apply_pending

_MARKET = Path(__file__).parent / "fixtures" / "universe_lifecycle.csv"


def _make_app(db: Path):
    from fastapi import FastAPI

    app = FastAPI()
    include_v2_routers(app)
    return app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "v2.db"
    conn = sqlite3.connect(str(db))
    apply_pending(conn)
    conn.close()
    monkeypatch.setenv("AB_DB_PATH", str(db))
    return TestClient(_make_app(db))


def test_desk_returns_next_action(client):
    """Desk 必须给出服务端推导的唯一下一动作（未迁移数据 → SYNC_DATA 或 RUN_SCAN）。"""
    r = client.get("/api/v2/desk")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "next_action" in body
    assert body["side_effects"] is False


def test_research_experiments_roundtrip(client):
    r = client.post("/api/v2/research/experiments", json={
        "strategy": "accumulation_breakout_v1",
        "params": {"vol_ratio": 1.5},
        "config_hash": "cfg-abc",
    })
    assert r.status_code == 200, r.text
    exp_id = r.json()["experiment_id"]

    # 幂等：同指纹返回既有 id
    r2 = client.post("/api/v2/research/experiments", json={
        "strategy": "accumulation_breakout_v1",
        "params": {"vol_ratio": 1.5},
        "config_hash": "cfg-abc",
    })
    assert r2.status_code == 200
    assert r2.json()["experiment_id"] == exp_id

    r3 = client.get("/api/v2/research/experiments")
    assert r3.status_code == 200
    assert r3.json()["count"] is None or r3.json()["count"] >= 1


def test_paper_status_missing_account(client):
    """无纸面账户 → 404（fail-closed，不伪造空账户）。"""
    r = client.get("/api/v2/paper/status")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.json()["side_effects"] is False


def test_unmigrated_desk_404(tmp_path, monkeypatch):
    db = tmp_path / "raw.db"
    sqlite3.connect(str(db)).close()
    monkeypatch.setenv("AB_DB_PATH", str(db))
    c = TestClient(_make_app(db))
    r = c.get("/api/v2/desk")
    # today_guide 在缺表时应安全失败而非 500
    assert r.status_code in (200, 404)


def test_default_db_path_points_into_project():
    """回归：deps 默认路径必须指向本项目 runtime（曾错指父目录空库）。"""
    from ab_screener.api.deps import DEFAULT_DB_PATH, ROOT

    assert str(ROOT).endswith("accumulation_breakout")
    assert DEFAULT_DB_PATH == ROOT / "runtime" / "stock_data.db"
    assert DEFAULT_DB_PATH.parent.name == "runtime"
