"""T02 龙虎榜数据源适配与幂等抓取（全程 fake client，无 Token / 网络）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ab_screener.application.lhb_ingest import ingest_with_fallback, missing_trade_dates, persist_fetch
from ab_screener.data.adapters.lhb_sources import (
    OFFICIAL_FAIL_REASON,
    CircuitBreaker,
    FetchResult,
    LhbHtmlChanged,
    LhbRateLimited,
    LhbTimeout,
    OfficialExchangeAdapter,
    TushareLhbAdapter,
    rows_content_sha256,
    with_fallback,
)
from ab_screener.data.migration_registry import apply_pending

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "lhb"
SECRET = "secret-token-xyz"
TS = "2026-08-10T16:00:00+08:00"


class FakePro:
    token = SECRET

    def __init__(self, tables: dict | None = None, *, fail_times: int = 0, fail_exc: Exception | None = None):
        self.tables = tables or {}
        self.calls = 0
        self.fail_times = fail_times
        self.fail_exc = fail_exc or LhbTimeout("TIMEOUT")

    def _payload(self, name: str) -> pd.DataFrame:
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.fail_exc
        rows = self.tables.get(name, [])
        return pd.DataFrame(rows)

    def top_list(self, trade_date: str, **_k: object) -> pd.DataFrame:
        return self._payload("top_list")

    def top_inst(self, trade_date: str, **_k: object) -> pd.DataFrame:
        return self._payload("top_inst")

    def hm_list(self, **_k: object) -> pd.DataFrame:
        return self._payload("hm_list")


def _adapter(fake: FakePro, **kwargs: object) -> TushareLhbAdapter:
    return TushareLhbAdapter(fake, now_iso=lambda: TS, sleeper=kwargs.pop("sleeper", lambda _s: None), **kwargs)


def _conn(tmp_path: Path):
    conn = __import__("sqlite3").connect(str(tmp_path / "lhb.db"))
    apply_pending(conn)
    return conn


def _top_list_row(ts_code: str = "000001.SZ") -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "trade_date": "20260810",
        "reason": "日涨幅偏离值达到7%",
        "amount": 1_000_000,
        "l_sell": 200_000,
        "l_buy": 300_000,
        "l_amount": 500_000,
        "net_amount": 100_000,
    }


def test_complete_and_idempotent_manifest(tmp_path: Path):
    data = json.loads((FIXTURE_DIR / "source_status.json").read_text(encoding="utf-8"))
    fake = FakePro({"top_inst": data["complete_top_inst"]})
    adapter = _adapter(fake)
    first = adapter.fetch("top_inst", "20260810", published=True)
    assert first.source_status == "COMPLETE"
    assert first.row_count == 1
    assert SECRET not in json.dumps(first.summary, ensure_ascii=False)
    conn = _conn(tmp_path)
    try:
        a = persist_fetch(conn, first)
        b = persist_fetch(conn, adapter.fetch("top_inst", "20260810", published=True))
        assert a["skipped"] is False
        assert b["skipped"] is True
        assert a["manifest_id"] == b["manifest_id"]
        n = conn.execute("SELECT COUNT(*) FROM top_inst_history").fetchone()[0]
        m = conn.execute("SELECT COUNT(*) FROM lhb_ingest_manifests").fetchone()[0]
        raw = conn.execute(
            "SELECT COUNT(*) FROM raw_ingest_manifests WHERE dataset='top_inst'"
        ).fetchone()[0]
        assert n == 1 and m == 1 and raw == 1
    finally:
        conn.close()


def test_status_fixtures_valid_empty_not_published_fetch_failed(tmp_path: Path):
    empty = FakePro({"top_inst": []})
    valid = _adapter(empty).fetch("top_inst", "20260810", published=True)
    unpublished = _adapter(empty).fetch("top_inst", "20260810")
    timed = _adapter(FakePro(fail_times=9, fail_exc=LhbTimeout("TIMEOUT")))
    failed = timed.fetch("top_inst", "20260810")
    assert valid.source_status == "VALID_EMPTY" and valid.row_count == 0
    assert unpublished.source_status == "NOT_PUBLISHED"
    assert failed.source_status == "FETCH_FAILED"
    assert failed.error_reason == "TIMEOUT"
    conn = _conn(tmp_path)
    try:
        persist_fetch(conn, valid)
        persist_fetch(conn, unpublished)
        persist_fetch(conn, failed)
        statuses = {
            r[0]
            for r in conn.execute("SELECT source_status FROM lhb_ingest_manifests")
        }
        assert statuses == {"VALID_EMPTY", "NOT_PUBLISHED", "FETCH_FAILED"}
        assert conn.execute("SELECT COUNT(*) FROM top_inst_history").fetchone()[0] == 0
    finally:
        conn.close()


def test_timeout_rate_limit_missing_field_html_not_written_as_success_empty(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        missing = FakePro({"top_inst": [{"ts_code": "000001.SZ", "trade_date": "20260810", "buy": 1}]})
        miss = _adapter(missing).fetch("top_inst", "20260810")
        assert miss.source_status == "FETCH_FAILED"
        assert "必需字段" in (miss.error_reason or "")
        assert "net_buy" in (miss.error_reason or "")
        official = OfficialExchangeAdapter("SH")
        blocked = official.fetch("top_inst", "20260810")
        assert blocked.source_status == "FETCH_FAILED"
        assert "fail-closed" in (blocked.error_reason or "")
        assert blocked.row_count == 0

        class Boom:
            def fetch(self, **_k):
                raise LhbHtmlChanged("HTML_STRUCTURE_CHANGED")

        changed = OfficialExchangeAdapter("SZ", client=Boom()).fetch("top_list", "20260810")
        assert changed.source_status == "FETCH_FAILED"
        persist_fetch(conn, miss)
        persist_fetch(conn, blocked)
        persist_fetch(conn, changed)
        assert conn.execute("SELECT COUNT(*) FROM top_inst_history").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM lhb_ingest_manifests WHERE source_status='COMPLETE'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_retry_capped_and_last_error_kept():
    sleeps: list[float] = []
    fake = FakePro(fail_times=9, fail_exc=LhbRateLimited("RATE_LIMITED"))
    result = _adapter(fake, max_attempts=3, sleeper=sleeps.append).fetch("top_list", "20260810")
    assert result.source_status == "FETCH_FAILED"
    assert result.error_reason == "RATE_LIMITED"
    assert fake.calls == 3
    assert len(sleeps) == 2


def test_retry_then_success():
    fake = FakePro(
        {"top_list": [_top_list_row()]},
        fail_times=2,
        fail_exc=LhbTimeout("TIMEOUT"),
    )
    result = _adapter(fake, max_attempts=3).fetch("top_list", "20260810", published=True)
    assert result.source_status == "COMPLETE"
    assert fake.calls == 3


def test_default_retry_uses_real_backoff_hook(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("ab_screener.data.adapters.lhb_sources.time.sleep", sleeps.append)
    fake = FakePro(
        {"top_list": [_top_list_row()]},
        fail_times=2,
        fail_exc=LhbTimeout("TIMEOUT"),
    )
    adapter = TushareLhbAdapter(fake, max_attempts=3, now_iso=lambda: TS)
    result = adapter.fetch("top_list", "20260810", published=True)
    assert result.source_status == "COMPLETE"
    assert sleeps == [1, 2]


def test_row_hash_is_stable_when_api_row_order_changes():
    rows = [_top_list_row("000001.SZ"), _top_list_row("000002.SZ")]
    assert rows_content_sha256(rows) == rows_content_sha256(list(reversed(rows)))


def test_top_list_missing_amount_fields_is_fetch_failed():
    fake = FakePro(
        {"top_list": [{"ts_code": "000001.SZ", "trade_date": "20260810", "reason": "r"}]}
    )
    result = _adapter(fake).fetch("top_list", "20260810", published=True)
    assert result.source_status == "FETCH_FAILED"
    assert "amount" in (result.error_reason or "")


@pytest.mark.parametrize(
    ("dataset", "row", "needle"),
    [
        ("top_list", dict(_top_list_row(), amount=float("inf")), "有限非负数"),
        ("top_list", dict(_top_list_row(), l_buy=-1), "有限非负数"),
        (
            "top_inst",
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260810",
                "exalter": "机构专用",
                "side": "0",
                "buy": float("nan"),
                "sell": 0,
                "net_buy": 0,
                "reason": "日涨幅偏离值达到7%",
            },
            "不能为空",
        ),
        (
            "top_inst",
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260810",
                "exalter": "机构专用",
                "side": "1",
                "buy": 0,
                "sell": -1,
                "net_buy": 1,
                "reason": "日涨幅偏离值达到7%",
            },
            "有限非负数",
        ),
    ],
)
def test_invalid_lhb_amounts_fail_closed(dataset: str, row: dict[str, object], needle: str):
    result = _adapter(FakePro({dataset: [row]})).fetch(dataset, "20260810", published=True)
    assert result.source_status == "FETCH_FAILED"
    assert needle in (result.error_reason or "")


def test_circuit_open_stops_calls():
    fake = FakePro(fail_times=9, fail_exc=LhbTimeout("TIMEOUT"))
    adapter = _adapter(fake, breaker=CircuitBreaker(threshold=1), max_attempts=1)
    first = adapter.fetch("top_inst", "20260810")
    calls = fake.calls
    second = adapter.fetch("top_inst", "20260810")
    assert first.source_status == "FETCH_FAILED"
    assert second.error_reason == "CIRCUIT_OPEN"
    assert fake.calls == calls


def test_official_default_fail_closed_no_bypass():
    assert "captcha" in OFFICIAL_FAIL_REASON.lower() or "fail-closed" in OFFICIAL_FAIL_REASON
    result = OfficialExchangeAdapter("SH").fetch("top_list", "20260810")
    assert result.source_status == "FETCH_FAILED"
    assert result.row_count == 0


def test_fallback_marks_degraded_not_complete():
    primary = FetchResult(
        source="tushare",
        dataset="top_inst",
        partition_key="20260810",
        source_status="FETCH_FAILED",
        rows=(),
        row_count=0,
        content_sha256="x",
        available_at=TS,
        error_reason="TIMEOUT",
        summary={"row_count": 0, "columns": []},
    )
    secondary = FetchResult(
        source="official_sz",
        dataset="top_inst",
        partition_key="20260810",
        source_status="COMPLETE",
        rows=({"ts_code": "000001.SZ", "trade_date": "20260810", "exalter": "机构专用",
               "reason": "UNKNOWN", "side": "BUY", "buy": 1, "sell": 0},),
        row_count=1,
        content_sha256="y",
        available_at=TS,
        summary={"row_count": 1, "columns": ["ts_code"]},
    )
    merged = with_fallback(primary, secondary)
    assert merged.source_status == "DEGRADED"
    assert merged.row_count == 1


def test_calendar_holes_not_max_date(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        fake = FakePro({"top_list": [_top_list_row()]})
        persist_fetch(conn, _adapter(fake).fetch("top_list", "20260810", published=True))
        missing = missing_trade_dates(
            conn,
            dataset="top_list",
            source="tushare",
            calendar_dates=["20260806", "20260807", "20260810"],
        )
        assert missing == ["20260806", "20260807"]
    finally:
        conn.close()


def test_ingest_fallback_persists_degraded(tmp_path: Path):
    conn = _conn(tmp_path)
    try:
        primary = _adapter(FakePro(fail_times=9)).fetch("top_inst", "20260810")
        data = json.loads((FIXTURE_DIR / "source_status.json").read_text(encoding="utf-8"))
        secondary = _adapter(FakePro({"top_inst": data["complete_top_inst"]})).fetch(
            "top_inst", "20260810", published=True
        )
        out = ingest_with_fallback(conn, primary, secondary)
        assert out["source_status"] == "DEGRADED"
        assert conn.execute("SELECT COUNT(*) FROM top_inst_history").fetchone()[0] == 1
    finally:
        conn.close()


def test_top_inst_side_zero_one_written_as_buy_sell(tmp_path: Path):
    fake = FakePro(
        {
            "top_inst": [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260810",
                    "exalter": "机构专用",
                    "side": "0",
                    "buy": 12.5,
                    "sell": 0,
                    "net_buy": 12.5,
                    "reason": "日涨幅偏离值达到7%",
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260810",
                    "exalter": "机构专用",
                    "side": "1",
                    "buy": 0,
                    "sell": 3,
                    "net_buy": -3,
                    "reason": "日涨幅偏离值达到7%",
                },
            ]
        }
    )
    result = _adapter(fake).fetch("top_inst", "20260810", published=True)
    assert {row["side"] for row in result.rows} == {"BUY", "SELL"}
    conn = _conn(tmp_path)
    try:
        persist_fetch(conn, result)
        sides = {r[0] for r in conn.execute("SELECT side FROM top_inst_history")}
        assert sides == {"BUY", "SELL"}
        from ab_screener.application.lhb_reconcile import manifest_exists

        assert manifest_exists(conn, dataset="top_inst", partition_key="20260810")
        assert conn.execute(
            "SELECT COUNT(*) FROM lhb_ingest_manifests WHERE partition_key='20260810'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM raw_ingest_manifests WHERE partition_key='20260810'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_token_not_in_fetch_result_repr():
    fake = FakePro({"top_list": [_top_list_row()]})
    result = _adapter(fake).fetch("top_list", "20260810", published=True)
    blob = repr(result) + json.dumps(result.summary)
    assert SECRET not in blob
    assert not hasattr(result, "token")
