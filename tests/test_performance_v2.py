"""Deterministic unit-scale guards for the institutional performance budgets.

These tests stop obvious regressions on a disposable SQLite fixture.  They do
not replace the P8 machine/data-bound benchmark report against the frozen real
database snapshot.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from ab_screener.api.app_factory import include_v2_routers
from ab_screener.local_store import LocalStore
from paper_trading.account import opening_equity
from paper_trading.orders import list_orders

pytestmark = pytest.mark.performance

_BUDGET = json.loads(
    (Path(__file__).parent / "fixtures" / "performance_v2_budget.json").read_text(
        encoding="utf-8"
    )
)


def _p95_ms(call: Callable[[], Any], samples: int) -> float:
    durations: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        call()
        durations.append((time.perf_counter() - started) * 1000)
    durations.sort()
    rank = max(0, (95 * len(durations) + 99) // 100 - 1)
    return durations[rank]


def _business_dates(count: int, end: date = date(2026, 8, 26)) -> list[str]:
    dates: list[str] = []
    current = end
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y%m%d"))
        current -= timedelta(days=1)
    return list(reversed(dates))


@pytest.fixture()
def performance_db(tmp_path: Path) -> Path:
    db = tmp_path / "performance-v2.db"
    LocalStore(db_path=db)
    dates = _business_dates(20)
    codes = [f"{600000 + index:06d}.SH" for index in range(100)]
    now = "2026-08-26T16:30:00+08:00"

    daily_rows = []
    for code_index, code in enumerate(codes):
        base = 8.0 + code_index / 100
        for day_index, trade_date in enumerate(dates):
            close = base + day_index / 100
            daily_rows.append(
                (code, trade_date, close - 0.02, close + 0.05, close - 0.05,
                 close, 100_000.0, close * 100_000.0)
            )
    # Benchmark data keeps market-regime evaluation local and deterministic.
    daily_rows.extend(
        ("000300.SH", trade_date, 4000.0, 4010.0, 3990.0, 4000.0 + index,
         1_000_000.0, 4_000_000_000.0)
        for index, trade_date in enumerate(dates)
    )

    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO daily (ts_code,trade_date,open,high,low,close,vol,amount) "
            "VALUES (?,?,?,?,?,?,?,?)",
            daily_rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (cal_date,is_open,source,updated_at) "
            "VALUES (?,1,'local_infer',?)",
            [(trade_date, now) for trade_date in dates],
        )
        conn.executemany(
            "INSERT INTO scan_result (trade_date,ts_code,name,industry,price,mv_yi,pe,pb,"
            "turnover,box_days,box_amp,vol_ratio,fund_net_wan,fund_ratio,total_score,reasons,"
            "breakout_date,box_high,box_low,ma5,ma20,sig_calculated,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (dates[-1], code, f"样本{index:03d}", "测试行业", 10.0, 100.0, 15.0,
                 1.5, 2.0, 20, 0.08, 1.6, 500.0, 0.03, 90.0 - index / 10,
                 "[池A|strict] fixture", dates[-1], 10.2, 9.5, 10.0, 9.8, 1, now)
                for index, code in enumerate(codes)
            ],
        )
        conn.execute(
            "INSERT INTO pt_account (account_id,initial_cash_fen,status,config_version,"
            "created_at,updated_at) VALUES (1,100000000,'ACTIVE',1,?,?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO pt_cash_flow (account_id,kind,amount_fen,balance_fen,ref_id,occurred_at) "
            "VALUES (1,'INITIAL',100000000,100000000,NULL,?)",
            (now,),
        )

        position_orders = [
            (f"POS-{index:03d}", f"pos-key-{index:03d}", code, now, now)
            for index, code in enumerate(codes)
        ]
        conn.executemany(
            "INSERT INTO pt_order (order_id,idempotency_key,account_id,source,ts_code,side,qty,"
            "state,reserve_fen,reserved_qty,created_at,updated_at) "
            "VALUES (?,?,1,'PERFORMANCE_FIXTURE',?,'BUY',100,'FILLED',0,0,?,?)",
            position_orders,
        )
        conn.executemany(
            "INSERT INTO pt_fill (fill_id,order_id,ref_open_price_micro,fill_price_micro,qty,"
            "commission_fen,tax_fen,fill_model_version,quote_revision,filled_at) "
            "VALUES (?,?,10000000,10000000,100,500,0,'fixture-v1','fixture',?)",
            [(f"FILL-{index:03d}", f"POS-{index:03d}", now) for index in range(100)],
        )
        conn.executemany(
            "INSERT INTO pt_position_lot (account_id,ts_code,buy_fill_id,remaining_qty,"
            "cost_price_micro,sellable_date,created_at) VALUES (1,?,?,100,10000000,?,?)",
            [
                (code, f"FILL-{index:03d}", dates[-1], now)
                for index, code in enumerate(codes)
            ],
        )
        conn.executemany(
            "INSERT INTO pt_order (order_id,idempotency_key,account_id,source,ts_code,side,qty,"
            "state,reserve_fen,reserved_qty,created_at,updated_at) "
            "VALUES (?,?,1,'PERFORMANCE_FIXTURE',?,'BUY',100,'DRAFT',0,0,?,?)",
            [
                (f"ORD-{index:04d}", f"order-key-{index:04d}", codes[index % 100],
                 f"2026-08-26T16:{index % 60:02d}:00+08:00", now)
                for index in range(1000)
            ],
        )
    return db


def test_health_and_desk_hot_p95(performance_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AB_DB_PATH", str(performance_db))
    app = FastAPI()
    include_v2_routers(app, include_scan_router=False)
    client = TestClient(app)

    assert client.get("/api/v2/system/health").status_code == 200
    assert client.get("/api/v2/desk").status_code == 200
    samples = int(_BUDGET["samples"]["hot"])
    thresholds = _BUDGET["thresholds_ms"]
    assert _p95_ms(
        lambda: client.get("/api/v2/system/health").raise_for_status(), samples
    ) < thresholds["health_hot_p95"]
    assert _p95_ms(
        lambda: client.get("/api/v2/desk").raise_for_status(), samples
    ) < thresholds["desk_hot_p95"]


def test_overview_100_candidates_latency_and_payload(
    performance_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ab_screener.api.routers import legacy_market

    monkeypatch.setattr(legacy_market, "_store", LocalStore(performance_db))
    legacy_market._SIG_CACHE.clear()
    app = FastAPI()
    app.include_router(legacy_market.router)
    client = TestClient(app)

    def cold_request():
        legacy_market._OVERVIEW_CACHE["key"] = None
        legacy_market._OVERVIEW_CACHE["payload"] = None
        response = client.get("/api/overview?pool=A")
        response.raise_for_status()
        assert response.json()["count"] == 100
        return response

    cold_response = cold_request()
    assert len(cold_response.content) < _BUDGET["thresholds_bytes"]["overview_100"]
    thresholds = _BUDGET["thresholds_ms"]
    assert _p95_ms(cold_request, int(_BUDGET["samples"]["cold"])) < thresholds[
        "overview_cold_p95"
    ]
    assert _p95_ms(
        lambda: client.get("/api/overview?pool=A").raise_for_status(),
        int(_BUDGET["samples"]["hot"]),
    ) < thresholds["overview_hot_p95"]


def test_100_positions_and_1000_order_pagination_p95(performance_db: Path) -> None:
    equity = opening_equity(performance_db)
    assert equity["positions"] == 100
    page = list_orders(performance_db, limit=50, offset=950)
    assert len(page) == 50
    previous_page = list_orders(performance_db, limit=50, offset=900)
    assert {row["order_id"] for row in page}.isdisjoint(
        row["order_id"] for row in previous_page
    )
    thresholds = _BUDGET["thresholds_ms"]
    samples = int(_BUDGET["samples"]["hot"])
    assert _p95_ms(lambda: opening_equity(performance_db), samples) < thresholds[
        "positions_100_p95"
    ]
    assert _p95_ms(
        lambda: list_orders(performance_db, limit=50, offset=950), samples
    ) < thresholds["orders_1000_page_p95"]
