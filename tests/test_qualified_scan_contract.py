"""Complete qualification is independent of Top and publishes atomically."""
import copy
import json
import sqlite3

import pytest

from ab_screener.application.scan_audit import build_qualification_report, complete_scan_run
from ab_screener.application.scan_jobs import ScanJobStore
from ab_screener.application.scan_publication import publish_standalone_scan, read_scan_publication
from ab_screener.domain.profile import default_profile
from ab_screener.screener.orchestrator import run_scan
from local_store import LocalStore
from tests.test_screener_golden_result import AS_OF, _isolate_io, frozen_market_store  # noqa: F401

DATE = "20260911"
RULES = {"version": "daily-qualification-v1", "semantics": {"target_count": 20, "box_ladder_days": [125, 105, 84, 63, 42, 20], "build_watch": True}}


def candidate(code="000001.SZ", pool="A", tier="strict", complete=True):
    return {"trade_date": DATE, "ts_code": code, "name": "synthetic", "reasons": f"[池{pool}|{tier}|] evidence",
            "total_score": 80.0, "box_amp": 17.4, "pool": pool, "tier": tier, "qualified_pool": pool,
            "price": 10.0, "mv_yi": 100.0, "pe": 20.0, "pb": 2.0, "turnover": 3.0, "quote_as_of": DATE,
            "fund_window": {"complete": complete}, "data_missing_fields": None,
            "observation_group": pool if complete and tier != "data_incomplete" else "DATA_INCOMPLETE"}


def publish(store, rows, final=None, task="qualified", **changes):
    final = rows[:1] if final is None else final
    jobs = ScanJobStore(store.db_path)
    jobs.reserve_running(task, top_n=len(final), days=160)
    result = {"scan_candidates": final, "qualified_candidates": rows,
              "qualification_report": build_qualification_report(rows, RULES),
              "freshness": {"can_publish_a": True}, "regime": {"allow_new_entries": True}, "hits": len(rows)}
    result.update(changes)
    profile = default_profile()
    return complete_scan_run(store.db_path, run_id=task, task_id=task, as_of=DATE, days=160,
                             result=result, count_a=sum(row["pool"] == "A" for row in final),
                             count_b=sum(row["pool"] == "B" for row in final),
                             strategy_snapshot=profile.to_canonical_dict(), config_hash=profile.config_hash(),
                             code_version="test", research_mode="daily_research")


@pytest.fixture
def market(tmp_path):
    return LocalStore(tmp_path / "qualified.db")


def test_full_qualification_is_archived_and_top_overflow_keeps_lineage(market):
    rows = [candidate(), candidate("000002.SZ"), candidate("000003.SZ", "B", "relaxed"),
            candidate("000004.SZ", "B", "data_incomplete", False)]
    overflow = {**rows[1], "pool": "B", "reasons": rows[1]["reasons"].replace("池A", "池B")}
    assert publish(market, rows, [rows[0], overflow])
    full = read_scan_publication(market.db_path, stage="qualified")
    displayed = read_scan_publication(market.db_path)
    assert full["qualified_available"] and full["qualified_verified"]
    assert full["started_at"]
    assert len(full["candidates"]) == 4 and len(displayed["candidates"]) == 2
    assert full["qualification"]["counts"] == {"A": 2, "B": 2}
    assert full["qualification"]["groups"] == {"A": 2, "B": 1, "DATA_INCOMPLETE": 1}
    assert all(row["box_amp"] == 17.4 for row in full["candidates"])
    assert displayed["candidates"][1]["qualified_pool"] == "A"


def test_empty_full_snapshot_is_distinct_from_legacy_final_only(market):
    assert publish(market, [], [])
    empty = read_scan_publication(market.db_path, stage="qualified")
    assert empty["qualified_verified"] and empty["qualification"]["total"] == 0
    profile = default_profile()
    run_id = publish_standalone_scan(market.db_path, as_of=DATE, days=160, profile=profile,
                                     candidates=[], freshness={}, regime={})
    legacy = read_scan_publication(market.db_path, run_id, stage="qualified")
    assert legacy["qualified_available"] is False and legacy["qualified_verified"] is False
    assert legacy["verified"] and legacy["integrity_error"] is None
    assert legacy["candidates"] == []


@pytest.mark.parametrize("fault", ["duplicate", "date", "hash", "final_extra", "final_mutated", "funds", "group"])
def test_malformed_full_snapshot_never_publishes(market, fault):
    rows = [candidate(), candidate("000002.SZ", "B", "relaxed")]
    final = [copy.deepcopy(rows[0])]
    overrides = {}
    if fault == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif fault == "date":
        rows[1]["trade_date"] = "20260910"
    elif fault == "hash":
        overrides["qualification_report"] = {**build_qualification_report(rows, RULES), "hash": "invalid"}
    elif fault == "final_extra":
        final.append(candidate("000099.SZ"))
    elif fault == "final_mutated":
        final[0]["total_score"] = 99
    elif fault == "funds":
        rows[0].pop("fund_window")
    elif fault == "group":
        rows[1]["observation_group"] = "DATA_INCOMPLETE"
    with pytest.raises(ValueError):
        publish(market, rows, final, **overrides)
    with sqlite3.connect(market.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM scan_run_candidates").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM scan_result").fetchone()[0] == 0
    assert ScanJobStore(market.db_path).get("qualified")["status"] == "RUNNING"


def test_failure_after_full_rows_rolls_back_both_stages(market):
    with sqlite3.connect(market.db_path) as conn:
        conn.execute("CREATE TRIGGER fail_final BEFORE INSERT ON scan_run_candidates WHEN NEW.stage='final' BEGIN SELECT RAISE(ABORT, 'injected final failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected final"):
        publish(market, [candidate(), candidate("000002.SZ")])
    with sqlite3.connect(market.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_run_candidates").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 0
    assert ScanJobStore(market.db_path).get("qualified")["status"] == "RUNNING"


def test_corrupted_full_snapshot_is_explicitly_unverified(market):
    publish(market, [candidate(), candidate("000002.SZ")])
    with sqlite3.connect(market.db_path) as conn:
        row = candidate("000002.SZ")
        row["total_score"] = 999
        conn.execute("UPDATE scan_run_candidates SET payload_json=? WHERE stage='qualified' AND ts_code='000002.SZ'", (json.dumps(row),))
    full = read_scan_publication(market.db_path, stage="qualified")
    assert full["qualified_available"] and not full["qualified_verified"]
    assert full["qualification_integrity_error"]
    assert not full["verified"] and full["integrity_error"]
    assert full["candidates"] == []
    final = read_scan_publication(market.db_path)
    assert not final["verified"] and len(final["candidates"]) == 1


def test_capture_hook_observes_committed_success_and_failure_cannot_unpublish(market, monkeypatch):
    observed = []

    def fail_capture(path, run_id):
        full = read_scan_publication(path, run_id, stage="qualified")
        observed.append((full["qualified_verified"], len(full["candidates"]), ScanJobStore(path).get(run_id)["status"]))
        raise RuntimeError("injected capture failure")

    monkeypatch.setattr("ab_screener.application.forward_observations.capture_scan_publication", fail_capture)
    assert publish(market, [candidate(), candidate("000002.SZ")])
    assert observed == [(True, 2, "SUCCEEDED")]
    assert read_scan_publication(market.db_path)["qualified_verified"]


def test_cli_preserves_actual_start_and_full_payload(market):
    rows = [candidate(), candidate("000002.SZ")]
    actual_start = "2026-09-11T15:30:00+08:00"
    run_id = publish_standalone_scan(market.db_path, as_of=DATE, days=160, profile=default_profile(),
                                     candidates=rows[:1], qualified_candidates=rows,
                                     qualification_report=build_qualification_report(rows, RULES),
                                     freshness={"can_publish_a": True}, regime={"allow_new_entries": True},
                                     started_at=actual_start)
    full = read_scan_publication(market.db_path, run_id, stage="qualified")
    assert full["started_at"] == actual_start
    assert full["qualified_verified"] and len(full["candidates"]) == 2


def test_actual_scanner_qualification_is_top_invariant(frozen_market_store, tmp_path):  # noqa: F811
    # Clone only a synthetic fixture; 20 additional long-box candidates make the
    # former max(top,20) ladder bug observable at Top50 versus Top5/15.
    path = tmp_path / "larger-synthetic.db"
    with sqlite3.connect(frozen_market_store.db_path) as source, sqlite3.connect(path) as target:
        source.backup(target)
        for table in ("daily", "daily_basic", "moneyflow", "stock_basic"):
            columns = [row[1] for row in target.execute(f"PRAGMA table_info({table})")]
            code_index = columns.index("ts_code")
            originals = target.execute(f"SELECT * FROM {table} WHERE ts_code='000001.SZ'").fetchall()
            for i in range(20):
                for original in originals:
                    values = list(original)
                    values[code_index] = f"{100001+i:06}.SZ"
                    if "symbol" in columns:
                        values[columns.index("symbol")] = f"{100001+i:06}"
                    target.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
    store = LocalStore(path)
    results = [run_scan(store=store, as_of=AS_OF, workers=1, days=160, force=True, persist=False, top=top) for top in (5, 15, 50)]
    assert len({result["qualification_report"]["hash"] for result in results}) == 1
    assert results[0]["qualified_candidates"] == results[1]["qualified_candidates"] == results[2]["qualified_candidates"]
    qualified_a = [row["ts_code"] for row in results[0]["qualified_candidates"] if row["qualified_pool"] == "A"]
    assert len(qualified_a) >= 20
    assert "000002.SZ" not in qualified_a  # 45-day box must not qualify just because Top grows.
    assert len(results[0]["df_a"]) < len(results[-1]["df_a"])
    result = results[0]
    published = publish_standalone_scan(
        store.db_path, as_of=AS_OF, days=160, profile=default_profile(),
        candidates=result["scan_candidates"], qualified_candidates=result["qualified_candidates"],
        qualification_report=result["qualification_report"], freshness=result["freshness"],
        regime=result["regime"], input_dataset_version=result["input_dataset_version"], pool_report=result["pool_report"],
    )
    publication = read_scan_publication(store.db_path, published, stage="qualified")
    assert publication["qualified_verified"]
    assert publication["pool_report"] == result["pool_report"]
    for result in results:
        assert result["qualification_report"]["rules_snapshot"]["semantics"]["box_ladder_days"] == [125, 105, 84, 63, 42, 20]
        assert "_trade_card" not in result["df_a"].columns
        assert all(not any(key in row for key in ("stop_price", "target_price", "position_pct", "trade_card")) for row in result["scan_candidates"])


def test_cancelled_scan_never_archives_full_qualification(market):
    jobs = ScanJobStore(market.db_path)
    jobs.reserve_running("cancelled-full", top_n=15, days=160)
    jobs.request_cancel("cancelled-full")
    rows = [candidate()]
    profile = default_profile()
    assert not complete_scan_run(market.db_path, run_id="cancelled-full", task_id="cancelled-full", as_of=DATE, days=160,
                                 result={"scan_candidates": rows, "qualified_candidates": rows,
                                         "qualification_report": build_qualification_report(rows, RULES),
                                         "freshness": {"can_publish_a": True}, "regime": {"allow_new_entries": True}},
                                 count_a=1, count_b=0, strategy_snapshot=profile.to_canonical_dict(),
                                 config_hash=profile.config_hash(), code_version="test", research_mode="daily_research")
    with sqlite3.connect(market.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_run_candidates").fetchone()[0] == 0
    assert jobs.get("cancelled-full")["status"] == "CANCELLED"


@pytest.mark.parametrize("complete_protocol", [True, False])
def test_child_result_preserves_full_protocol_or_fails_explicitly(tmp_path, monkeypatch, complete_protocol):
    import scan_job_runner

    profile = default_profile()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(profile.to_json(), encoding="utf-8")
    output = tmp_path / "child.result.json"
    rows = [candidate(), candidate("000002.SZ")]
    report = build_qualification_report(rows, RULES)
    result = {"scan_candidates": rows[:1], "qualified_candidates": rows, "qualification_report": report,
              "input_dataset_version": "frozen-test", "hits": [row["ts_code"] for row in rows]}
    if not complete_protocol:
        result.pop("qualified_candidates")
    monkeypatch.setattr("run_screener.run_scan", lambda **_: result)
    monkeypatch.setattr(scan_job_runner, "_configure_console_encoding", lambda: None)
    monkeypatch.setattr(scan_job_runner.sys, "argv", [
        "scan_job_runner.py", "--task-id", "child", "--profile", str(profile_path),
        "--result", str(output), "--progress", str(tmp_path / "progress.json"),
        "--cancel-file", str(tmp_path / "cancel"),
    ])
    status = scan_job_runner.main()
    payload = json.loads(output.read_text(encoding="utf-8"))
    if complete_protocol:
        assert status == 0 and payload["status"] == "ok"
        assert payload["qualified_candidates"] == rows
        assert payload["qualification_report"] == report
    else:
        assert status == 1 and payload["status"] == "error"
        assert "qualified_candidates" in payload["error"]


def test_cli_unknown_start_is_not_replaced_by_late_reservation_time(market):
    rows = [candidate()]
    run_id = publish_standalone_scan(market.db_path, as_of=DATE, days=160, profile=default_profile(),
                                     candidates=rows, qualified_candidates=rows,
                                     qualification_report=build_qualification_report(rows, RULES),
                                     freshness={"can_publish_a": True}, regime={"allow_new_entries": True})
    publication = read_scan_publication(market.db_path, run_id, stage="qualified")
    assert publication["qualified_verified"]
    assert publication["started_at"] is None


@pytest.mark.parametrize("fault", ["hash", "unknown_version", "unknown_scope", "null_metadata"])
def test_declared_but_invalid_qualification_fails_closed_for_default_reader(market, fault):
    publish(market, [candidate()])
    with sqlite3.connect(market.db_path) as conn:
        snapshot = json.loads(conn.execute("SELECT strategy_snapshot_json FROM scan_runs WHERE run_id='qualified'").fetchone()[0])
        qualification = snapshot["_publication"]["qualification"]
        if fault == "hash":
            qualification["hash"] = "tampered"
        elif fault == "unknown_version":
            qualification["version"] = 999
        elif fault == "unknown_scope":
            qualification["scope"] = "UNSUPPORTED"
        else:
            snapshot["_publication"]["qualification"] = None
        conn.execute("UPDATE scan_runs SET strategy_snapshot_json=? WHERE run_id='qualified'", (json.dumps(snapshot),))
    for stage in ("final", "qualified"):
        publication = read_scan_publication(market.db_path, "qualified", stage=stage)
        assert publication["verified"] is False
        assert publication["qualified_verified"] is False
        assert publication["integrity_error"] == publication["qualification_integrity_error"]
        assert publication["integrity_error"]
        assert len(publication["candidates"]) == (1 if stage == "final" else 0)


@pytest.mark.parametrize("tier", ["strict", "relaxed", "theme_fill"])
@pytest.mark.parametrize("fault", ["stale_quote", "missing_price", "missing_pe"])
def test_every_candidate_tier_downgrades_quote_and_basic_gaps(market, tier, fault):
    import pandas as pd

    from ab_screener.screener.orchestrator import _annotate_candidate_data, _serialize_candidates
    from pool_select import qualified_pools

    raw = {"ts_code": "000001.SZ", "名称": "fixture", "行业": "半导体", "最新价": 10.,
           "总市值(亿)": 100., "PE(TTM)": 20., "PB": 2., "换手率%": 3., "筛选层级": tier,
           "入选理由": "original evidence", "综合分": 80., "fund_window": {"complete": True}}
    quote_dates = {"000001.SZ": DATE if fault != "stale_quote" else "20260910"}
    if fault == "missing_price":
        raw["最新价"] = None
    if fault == "missing_pe":
        raw["PE(TTM)"] = float("nan")
    annotated = _annotate_candidate_data([raw], DATE, quote_dates)
    full_a, full_b = qualified_pools(pd.DataFrame(annotated))
    assert full_a.empty and len(full_b) == 1
    rows = _serialize_candidates((("B", full_b),), DATE, {}, quote_dates)
    row = rows[0]
    assert row["tier"] == "data_incomplete" and row["source_tier"] == tier
    assert row["observation_group"] == "DATA_INCOMPLETE"
    assert row["data_missing_fields"] == [{"stale_quote": "quote_as_of", "missing_price": "price", "missing_pe": "pe"}[fault]]
    assert publish(market, rows)
    assert read_scan_publication(market.db_path, stage="qualified")["qualification"]["groups"]["DATA_INCOMPLETE"] == 1


@pytest.mark.parametrize("fault", ["stale_quote", "missing_price", "missing_pe"])
def test_qualified_validation_independently_rejects_hidden_data_gaps(market, fault):
    row = candidate(pool="B", tier="theme_fill")
    if fault == "stale_quote":
        row["quote_as_of"] = "20260910"
    elif fault == "missing_price":
        row["price"] = None
    else:
        row["pe"] = None
    with pytest.raises(ValueError, match="quote/basic missing"):
        publish(market, [row])
    assert read_scan_publication(market.db_path) is None


def test_full_theme_mode_has_no_display_or_800_candidate_cap(monkeypatch):
    import pandas as pd

    from ab_screener.screener import evaluator

    codes = [f"{100000+i:06}.SZ" for i in range(850)]
    candidates = pd.DataFrame({"ts_code": codes, "name": codes, "industry": ["theme"] * len(codes)})
    daily = pd.DataFrame({"ts_code": codes, "trade_date": [DATE] * len(codes), "close": [10.] * len(codes)})
    monkeypatch.setattr(evaluator, "theme_universe_mask", lambda frame, _: pd.Series(True, index=frame.index))
    monkeypatch.setattr(evaluator, "match_themes", lambda *args: ["theme"])
    monkeypatch.setattr(evaluator, "_soft_setup_row", lambda code, *args, **kwargs: {
        "ts_code": code, "名称": code, "行业": "theme", "综合分": 80., "筛选层级": "theme_fill"})
    monkeypatch.setattr(evaluator.data_fetch, "get_moneyflow_by_dates", lambda *args, **kwargs: pytest.fail("full qualification must keep missing funds explicit, without online fetch"))
    common = {"shortfall_themes": ["theme"], "theme_min": {"theme": 5}, "cand": candidates,
              "daily_sorted": daily, "basic_latest": candidates, "mf_by_code": {}, "mf_dates": [], "full_qualification": True}
    small = evaluator._theme_soft_fill(need_total=30, already=set(), sig_by_code={}, **common)
    large = evaluator._theme_soft_fill(need_total=50, already=set(), sig_by_code={}, **common)
    assert [row["ts_code"] for row in small] == [row["ts_code"] for row in large] == codes


def test_scanner_full_b_is_independent_of_top_and_marks_theme_data_gaps(frozen_market_store, tmp_path, monkeypatch):  # noqa: F811
    from ab_screener.screener import orchestrator

    path = tmp_path / "larger-theme-synthetic.db"
    with sqlite3.connect(frozen_market_store.db_path) as source, sqlite3.connect(path) as target:
        source.backup(target)
        for table in ("daily", "daily_basic", "moneyflow", "stock_basic"):
            columns = [row[1] for row in target.execute(f"PRAGMA table_info({table})")]
            originals = target.execute(f"SELECT * FROM {table} WHERE ts_code='000006.SZ'").fetchall()
            for i in range(60):
                for original in originals:
                    values = list(original)
                    values[columns.index("ts_code")] = f"{200001+i:06}.SZ"
                    if "symbol" in columns:
                        values[columns.index("symbol")] = f"{200001+i:06}"
                    target.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
        target.execute("UPDATE daily_basic SET pe=NULL WHERE ts_code='200001.SZ'")
        target.execute("DELETE FROM daily WHERE ts_code='200002.SZ' AND trade_date=?", (AS_OF,))
    store = LocalStore(path)
    results = []
    for top_b in (30, 50):
        monkeypatch.setattr(orchestrator, "TOP_N_WATCH", top_b)
        results.append(run_scan(store=store, as_of=AS_OF, workers=1, days=160, force=True, persist=False))
    assert len({result["qualification_report"]["hash"] for result in results}) == 1
    assert len(results[0]["df_b"]) == 30 and len(results[1]["df_b"]) == 50
    full = results[0]["qualified_candidates"]
    assert sum(row["qualified_pool"] == "B" for row in full) >= 61
    by_code = {row["ts_code"]: row for row in full}
    assert by_code["200001.SZ"]["data_missing_fields"] == ["pe"]
    assert by_code["200002.SZ"]["data_missing_fields"] == ["quote_as_of"]
    assert all(by_code[code]["observation_group"] == "DATA_INCOMPLETE" for code in ("200001.SZ", "200002.SZ"))
    assert results[0]["qualification_report"]["rules_snapshot"]["semantics"]["theme_qualification_quota"] is None


def test_publication_preserves_only_supplied_pool_report(market):
    report = {"qualified_strict": 1, "withheld_strict": 1, "a_slots": 0, "qualified_counts": {"A": 0, "B": 1}}
    row = candidate(pool="B", tier="strict")
    assert publish(market, [row], pool_report=report, regime={"allow_new_entries": False})
    assert read_scan_publication(market.db_path)["pool_report"] == report
    profile = default_profile()
    run_id = publish_standalone_scan(market.db_path, as_of=DATE, days=160, profile=profile,
                                     candidates=[], freshness={}, regime={})
    assert read_scan_publication(market.db_path, run_id)["pool_report"] is None
