"""T10 API：过滤、source_status 三分、金额单位元、as_of 字段。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ab_screener.api.app_factory import include_v2_routers
from ab_screener.data.lhb_repository import (
    save_profile_snapshot,
    save_signal_observation,
    save_signal_outcome,
)
from ab_screener.data.migration_intents.lhb_tracking_v2 import apply_lhb_tracking


def _app() -> FastAPI:
    app = FastAPI()
    include_v2_routers(app)
    return app


def _seed_manifest(conn: sqlite3.Connection, day: str, status: str, rows: int = 0) -> None:
    conn.execute(
        "INSERT INTO lhb_ingest_manifests (manifest_id, dataset, partition_key, source, revision,"
        " source_status, row_count, content_sha256, error_reason, available_at, ingested_at,"
        " payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"m-{day}-{status}",
            "top_list",
            day,
            "tushare",
            1,
            status,
            rows,
            "abc",
            None if status != "FETCH_FAILED" else "timeout",
            "2026-08-10T16:00:00+08:00",
            "2026-08-10T16:01:00+08:00",
            "{}",
        ),
    )


def _seed_event(conn: sqlite3.Connection, day: str = "20260810") -> None:
    conn.execute(
        "INSERT INTO lhb_event (event_id, revision, exchange, ts_code, window_code, reason_code,"
        " reason_raw, reason_catalog_version, disclose_date, period_start, period_end,"
        " flow_fingerprint, source, source_status, available_at, ingested_at, content_hash,"
        " payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ev-1",
            1,
            "SZ",
            "000001.SZ",
            "D1",
            "PCT_DEV_UP_1D",
            "日涨幅偏离值达7%",
            "v1",
            day,
            day,
            day,
            "fp1",
            "tushare",
            "COMPLETE",
            "2026-08-10T16:00:00+08:00",
            "2026-08-10T16:01:00+08:00",
            "hash1",
            '{"buy_yuan": 1013162595.79}',
        ),
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "lhb.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_lhb_tracking(conn)
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("AB_DB_PATH", str(db.resolve()))
    return TestClient(_app()), db


def test_not_published_vs_valid_empty_vs_failed(client):
    api, db = client
    conn = sqlite3.connect(str(db))
    try:
        _seed_manifest(conn, "20260810", "NOT_PUBLISHED")
        _seed_manifest(conn, "20260811", "VALID_EMPTY")
        _seed_manifest(conn, "20260812", "FETCH_FAILED")
        _seed_manifest(conn, "20260813", "COMPLETE", rows=1)
        _seed_event(conn, "20260813")
        conn.commit()
    finally:
        conn.close()
    unpublished = api.get("/api/v2/lhb/radar?trade_date=20260810").json()
    empty = api.get("/api/v2/lhb/radar?trade_date=20260811").json()
    failed = api.get("/api/v2/lhb/radar?trade_date=20260812").json()
    complete = api.get("/api/v2/lhb/radar?trade_date=20260813").json()
    assert unpublished["source_status"] == "NOT_PUBLISHED"
    assert unpublished["items"] == []
    assert empty["source_status"] == "VALID_EMPTY"
    assert empty["items"] == []
    assert failed["source_status"] == "FETCH_FAILED"
    assert failed["error_reason"]
    assert complete["source_status"] == "COMPLETE"
    assert complete["count"] == 1
    assert complete["amount_unit"] == "yuan"
    assert complete["as_of"].endswith("+08:00")
    assert complete["research_only"] is True
    yuan = complete["items"][0]["payload"]["buy_yuan"]
    assert yuan == 1013162595.79


def test_filters_and_versions(client):
    api, db = client
    conn = sqlite3.connect(str(db))
    try:
        _seed_manifest(conn, "20260810", "COMPLETE", rows=1)
        _seed_event(conn)
        save_profile_snapshot(
            conn,
            {
                "subject_type": "seat",
                "subject_id": "seat-a",
                "window_days": 60,
                "sample_size": 8,
                "display_win_rate": 0.6,
            },
            as_of="2026-08-10T16:00:00+08:00",
        )
        save_signal_observation(
            conn,
            {
                "observation_id": "obs-1",
                "ts_code": "000001.SZ",
                "disclose_date": "20260810",
                "disclose_at": "2026-08-10T16:00:00+08:00",
                "earliest_executable_at": "2026-08-11T09:30:00+08:00",
                "status": "WATCH",
                "scores": {"net_over_amount": 0.1},
                "vetoes": [],
                "policy_version": "lhb-signal-v1",
            },
        )
        conn.commit()
    finally:
        conn.close()
    ev = api.get("/api/v2/lhb/events?trade_date=20260810&ts_code=000001.SZ").json()
    assert ev["count"] == 1
    seat = api.get("/api/v2/lhb/seats/seat-a?as_of=2026-08-10T16:00:00+08:00").json()
    assert seat["source_status"] == "COMPLETE"
    assert seat["items"][0]["display_win_rate"] == 0.6
    seat_utc = api.get("/api/v2/lhb/seats/seat-a?as_of=2026-08-10T08:00:00Z").json()
    assert seat_utc["source_status"] == "COMPLETE"
    assert seat_utc["as_of"] == "2026-08-10T16:00:00+08:00"
    assert seat_utc["items"] == seat["items"]
    sig = api.get("/api/v2/lhb/signals?status=WATCH").json()
    assert sig["items"][0]["policy_version"] == "lhb-signal-v1"
    bt = api.get("/api/v2/lhb/backtest").json()
    assert bt["can_claim_edge"] is False
    assert bt["research_status"] == "RESEARCH_BLOCKED"


def test_schema_missing_is_fetch_failed_not_empty_array(tmp_path: Path, monkeypatch):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    monkeypatch.setenv("AB_DB_PATH", str(db.resolve()))
    r = TestClient(_app()).get("/api/v2/lhb/radar?trade_date=20260810")
    body = r.json()
    assert r.status_code == 200
    assert body["source_status"] == "FETCH_FAILED"
    assert body["items"] == []
    assert body["error_reason"] == "SCHEMA_MISSING"


def test_api_is_pit_correct_and_advanced_filters_are_real(client):
    api, db = client
    conn = sqlite3.connect(str(db))
    try:
        _seed_manifest(conn, "20260810", "COMPLETE", rows=1)
        _seed_event(conn)
        conn.execute(
            "INSERT INTO lhb_seat_trade(event_id,seat_raw,seat_id,revision,buy_amount_fen,"
            " sell_amount_fen,net_amount_fen,source,available_at,ingested_at,content_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ev-1",
                "机构专用",
                "seat-inst",
                1,
                10_000_000,
                0,
                10_000_000,
                "test",
                "2026-08-10T15:30:00+08:00",
                "2026-08-10T15:30:00+08:00",
                "trade-1",
            ),
        )
        conn.execute(
            "INSERT INTO actor_master(actor_id,revision,actor_type,display_name,valid_from,source,"
            " available_at,ingested_at,content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "actor-inst",
                1,
                "INSTITUTION_CHANNEL",
                "机构通道假设",
                "20200101",
                "test",
                "2026-08-10T15:00:00+08:00",
                "2026-08-10T15:00:00+08:00",
                "actor-1",
            ),
        )
        conn.execute(
            "INSERT INTO seat_actor_hypothesis(seat_id,actor_id,revision,valid_from,confidence,"
            " evidence_grade,evidence_source,conflict_status,source,available_at,ingested_at,"
            " content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "seat-inst",
                "actor-inst",
                1,
                "20200101",
                0.8,
                "A",
                "test",
                "NONE",
                "test",
                "2026-08-10T15:00:00+08:00",
                "2026-08-10T15:00:00+08:00",
                "hyp-1",
            ),
        )
        # 次日才到达的修订不得改变 8 月 10 日雷达。
        conn.execute(
            "INSERT INTO lhb_event(event_id,revision,exchange,ts_code,window_code,reason_code,"
            " reason_raw,reason_catalog_version,disclose_date,source,source_status,available_at,"
            " ingested_at,content_hash,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ev-1",
                2,
                "SZ",
                "000001.SZ",
                "D1",
                "PCT_DEV_UP_1D",
                "未来修订",
                "v1",
                "20260810",
                "test",
                "COMPLETE",
                "2026-08-11T09:00:00+08:00",
                "2026-08-11T09:00:00+08:00",
                "event-2",
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    body = api.get(
        "/api/v2/lhb/events",
        params={
            "trade_date": "20260810",
            "actor_type": "INSTITUTION_CHANNEL",
            "min_confidence": 0.7,
            "as_of": "2026-08-10T16:00:00+08:00",
        },
    ).json()
    assert body["count"] == 1
    assert body["items"][0]["revision"] == 1
    assert body["items"][0]["reason_raw"] != "未来修订"
    excluded = api.get(
        "/api/v2/lhb/events",
        params={
            "trade_date": "20260810",
            "min_confidence": 0.9,
            "as_of": "2026-08-10T16:00:00+08:00",
        },
    ).json()
    assert excluded["count"] == 0


def test_network_and_backtest_endpoints_use_persisted_facts(client):
    api, db = client
    conn = sqlite3.connect(str(db))
    try:
        _seed_manifest(conn, "20260810", "COMPLETE", rows=1)
        _seed_event(conn)
        for seat, actor, net in (("seat-a", "actor-shared", 1000), ("seat-b", "actor-shared", 2000), ("seat-c", "actor-c", 3000)):
            conn.execute(
                "INSERT INTO lhb_seat_trade(event_id,seat_raw,seat_id,revision,buy_amount_fen,"
                " sell_amount_fen,net_amount_fen,source,available_at,ingested_at,content_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("ev-1", seat, seat, 1, net, 0, net, "test", "2026-08-10T15:00:00+08:00", "2026-08-10T15:00:00+08:00", seat),
            )
            conn.execute(
                "INSERT INTO seat_actor_hypothesis(seat_id,actor_id,revision,valid_from,confidence,"
                " evidence_grade,evidence_source,conflict_status,hypothesis_note,source,available_at,"
                " ingested_at,content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (seat, actor, 1, "20200101", 0.7, "B", "test", "NONE", actor, "test", "2026-08-10T15:00:00+08:00", "2026-08-10T15:00:00+08:00", f"h-{seat}"),
            )
        save_signal_observation(
            conn,
            {
                "observation_id": "obs-bt",
                "ts_code": "000001.SZ",
                "disclose_date": "20260810",
                "disclose_at": "2026-08-10T16:00:00+08:00",
                "earliest_executable_at": "2026-08-11T09:30:00+08:00",
                "status": "WATCH",
                "scores": {},
                "vetoes": [],
            },
        )
        save_signal_outcome(
            conn,
            observation_id="obs-bt",
            horizon_days=1,
            status="MATURED",
            entry_fillable=1,
            gross_return=0.02,
            net_return=0.015,
            benchmark_excess=0.01,
            available_at="2026-08-12T16:00:00+08:00",
        )
        conn.commit()
    finally:
        conn.close()

    network = api.get("/api/v2/lhb/network", params={"trade_date": "20260810"}).json()
    assert network["independent_actor_count"] == 2
    assert network["count"] == 1
    assert network["items"][0]["weight"] == 1
    backtest = api.get("/api/v2/lhb/backtest").json()
    assert backtest["source_status"] == "COMPLETE"
    assert backtest["horizons"]["1"]["net_return"] == 0.015
    assert backtest["can_claim_edge"] is False
