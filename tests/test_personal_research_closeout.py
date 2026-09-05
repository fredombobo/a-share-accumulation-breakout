from __future__ import annotations

import copy
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from ab_screener.operations.runtime_identity import verify_identity
from ab_screener.research.data_scope import inspect_scope
from ab_screener.research.portfolio_accounting import PortfolioPolicy, simulate_portfolio
from ab_screener.research.professional_grid import ProfessionalGridError, resolve_universe
from ab_screener.research.result_details import build_details


def test_current_universe_sampling_is_stratified_deterministic_and_explicit_not_truncated(monkeypatch):
    import ab_screener.research.professional_grid as grid

    rows = [{"ts_code": f"{n:06d}.{exchange}", "industry": f"行业{n % 4}"}
            for exchange in ("SH", "SZ") for n in range(1, 101)]
    monkeypatch.setattr(grid, "_stock_rows", lambda _: rows)
    first = resolve_universe("unused", max_codes=20)
    rows.reverse()
    second = resolve_universe("unused", max_codes=20)
    assert first == second
    assert first["sampling"]["sample_exchanges"] == {"SH": 10, "SZ": 10}
    assert len(first["sampling"]["sample_industries"]) == 4
    codes = [row["ts_code"] for row in rows[:25]]
    with pytest.raises(ProfessionalGridError, match="不会静默截断"):
        resolve_universe("unused", max_codes=20, codes=codes)
    assert resolve_universe("unused", max_codes=25, codes=codes)["codes"] == sorted(codes)


def test_identity_requires_product_root_database_build_and_literal_live_false(tmp_path):
    root, db = str(tmp_path), str(tmp_path / "market.db")
    good = {"product": "accumulation_breakout", "port": 8001, "build_version": "abc",
            "repository_root": root, "database_path": db, "live_trading_enabled": False}
    verify_identity(good, root, db, "abc")
    for key, bad in (("product", "aetf"), ("port", 8000), ("build_version", "old"),
                     ("repository_root", str(tmp_path / "wrong")), ("database_path", "wrong.db"),
                     ("live_trading_enabled", "false"), ("live_trading_enabled", 0),
                     ("live_trading_enabled", True)):
        with pytest.raises(ValueError):
            verify_identity({**good, key: bad}, root, db, "abc")


def test_scope_checks_warmup_without_writing_business_tables(tmp_path: Path):
    db = tmp_path / "scope.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE daily(ts_code,trade_date,effective_at,available_at,ingested_at,source,revision)")
        conn.execute("CREATE TABLE daily_history(ts_code,trade_date,available_at)")
        conn.execute("CREATE TABLE instrument_lifecycle_history(ts_code)")
        conn.execute("CREATE TABLE pt_account(cash_fen)")
        conn.execute("INSERT INTO pt_account VALUES(10000000)")
        for day in ("20240102", "20240801"):
            conn.execute("INSERT INTO daily VALUES(?,?,?,?,?,?,?)", ("000001.SZ", day, day, day, day, "source", 1))
            conn.execute("INSERT INTO daily_history VALUES(?,?,?)", ("000001.SZ", day, day))
    args = (db, ["000001.SZ"], "20240801", "20240830", 540)
    assert inspect_scope(*args)["can_run"] is True
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE daily SET available_at=NULL WHERE trade_date='20240102'")
    failed = inspect_scope(*args)
    assert failed["can_run"] is False
    assert failed["missing_metadata"] == 1
    assert failed["last_incomplete_date"] == "20240102"
    assert failed == inspect_scope(*args)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT cash_fen FROM pt_account").fetchall() == [(10000000,)]
        assert conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 2


def test_wf_preserves_small_count_without_lowering_minimum(monkeypatch):
    import walkforward

    frame = pd.DataFrame()
    frame.attrs["diagnostic_rows"] = [{"net_n_trades": 12, "net_profit_factor": 1.5,
        "net_max_drawdown": 0.05, "portfolio_status": "PASS", "net_win_rate": 0.5,
        "sample_diagnostic": {"code": "BELOW_MINIMUM_TRADES", "completed_trades": 12}}]
    monkeypatch.setattr(walkforward, "run_grid", lambda **_: frame)
    combo = {"strategy": "A", "vol_ratio_min": 1.5, "strong_reset": 3, "exit_window": 10,
             "stop_pct": 0.07, "target_pct": 0.12, "max_hold_days": 30}
    assert walkforward.eval_combo(combo, "20240101", "20241231")["net_n_trades"] == 12
    result = walkforward.wf_recheck([combo], portfolio_policy=PortfolioPolicy()).iloc[0]
    assert not result["wf_pass"]
    assert not result["evidence_complete"]
    assert result["oos_mean_pf"] is None
    assert all(row["test_n"] == 12 for row in result["wf_detail"])


def test_account_details_reconcile_exact_cash_months_and_realized_contributions():
    market = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": date, "open": px,
        "close": px, "high": px + 1, "low": px - 1, "vol": 100000, "amount": 1000000,
        "pre_close": px} for date, px in (("20260730", 10), ("20260731", 10), ("20260803", 11), ("20260804", 12))])
    trade = {"ts_code": "000001.SZ", "date": "20260730", "entry_date": "2026-07-31",
             "exit_date": "2026-08-04", "exit": "time", "exit_price": 12, "cost": {"filled": True}}
    portfolio = simulate_portfolio([trade], market, policy=PortfolioPolicy())
    details = build_details(portfolio, {"000001.SZ": "银行"})
    assert details["reconciliation"] == "EXACT_FEN"
    total = int(details["final_equity_fen"]) - int(details["initial_equity_fen"])
    assert sum(int(row["net_pnl_fen"]) for row in details["monthly"]) == total
    assert sum(int(row["realized_pnl_fen"]) for row in details["stock_contribution"]) == total
    assert details["unrealized_pnl_fen"] == "0"
    assert len(details["monthly"]) == 2
    broken = copy.deepcopy(portfolio)
    broken["portfolio_final_equity_fen"] += 1
    with pytest.raises(ValueError):
        build_details(broken, {})


def test_daily_script_legacy_maintenance_is_opt_in_and_no_process_path_comparison():
    script = (Path(__file__).resolve().parents[1] / "daily_run.ps1").read_text(encoding="utf-8-sig")
    assert script.count("if (-not $InstitutionalMaintenance)") == 2
    assert "$op.Path -ne $Python" not in script
    assert "runtime_identity --root $Root --db $DbPath" in script
    assert "personal_daily --db $DbPath --scan-result $rf" in script
    assert "$env:PYTHONIOENCODING = 'utf-8'" in script


def test_daily_receipt_uses_canonical_calendar_and_fails_closed(tmp_path, monkeypatch):
    import json

    import build_version
    from ab_screener.operations import personal_daily

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 5, 17, 0, tzinfo=tz)

    monkeypatch.setattr(personal_daily, "datetime", FrozenDatetime)
    monkeypatch.setattr(build_version, "build_version", lambda: "tested-build")
    db = tmp_path / "market.db"
    result = tmp_path / "scan_fixed.result.json"
    result.write_text(json.dumps({"status": "ok", "latest_date": "20260904"}), encoding="utf-8")
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE daily(trade_date);
            INSERT INTO daily VALUES('20260904');
            CREATE TABLE scan_jobs(task_id,status);
            INSERT INTO scan_jobs VALUES('fixed','SUCCEEDED');
            CREATE TABLE scan_runs(task_id,git_sha,as_of,status);
            INSERT INTO scan_runs VALUES('fixed','tested-build','20260904','SUCCEEDED');
            CREATE TABLE trade_cal(cal_date,is_open,source,updated_at);
            INSERT INTO trade_cal VALUES('20260904',1,'tushare','2026-09-05T17:00:00+08:00');
        """)
    with pytest.raises(ValueError, match="最新已完成交易日"):
        personal_daily.record_scan(db, result)
    assert not (tmp_path / "personal-daily").exists()
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO trade_cal VALUES('20260905',0,'tushare','2026-09-05T17:00:00+08:00')")
    first = personal_daily.record_scan(db, result)
    assert first["status"] == "DAILY_COMPLETE"
    assert personal_daily.record_scan(db, result) == first
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE scan_runs SET git_sha='old-build'")
    with pytest.raises(ValueError, match="当前版本"):
        personal_daily.record_scan(db, result)
