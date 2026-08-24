"""七类故障注入（§5）：同步失败/行情缺失/公司行为/撮合中断/对账差异/审计写失败/进程重启。

每类都断言：上游失败阻断全部下游、结构化错误、不产生 COMPLETE manifest。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.scheduler_repository import (
    acquire_lease,
    record_step_attempt,
    start_run,
)
from ab_screener.operations.dag import DAG_STEPS, build_eod_dag
from ab_screener.operations.scheduler import SchedulerRunner
from tests.test_daily_dag_closed_loop import TRADE_DATE, _build_closed_loop_db

pytestmark = pytest.mark.fault_injection


@pytest.fixture()
def db(tmp_path: Path) -> str:
    return _build_closed_loop_db(tmp_path)


def _runner(db: str, holder: str = "fault-test") -> SchedulerRunner:
    return SchedulerRunner(db, build_eod_dag(db, today=TRADE_DATE), holder=holder)


def _manifest_exists(db: str) -> bool:
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT 1 FROM daily_run_manifests WHERE trade_date=?", (TRADE_DATE,)
        ).fetchone()
    return row is not None


def _downstream_blocked(result: dict, failure_step: str) -> bool:
    """故障步骤之后的全部下游必须 SKIPPED，故障步骤本身必须 FAIL。"""
    if result.get(failure_step, {}).get("status") != "FAIL":
        return False
    idx = list(DAG_STEPS).index(failure_step)
    for step in list(DAG_STEPS)[idx + 1:]:
        if result.get(step, {}).get("status") != "SKIPPED":
            return False
    return True


def test_sync_gate_failure_blocks_downstream(db):
    """同步/门禁失败（当日非交易日）→ 全部下游阻断。"""
    with sqlite3.connect(db) as c:
        c.execute("UPDATE trade_cal SET is_open=0 WHERE cal_date=?", (TRADE_DATE,))
        c.commit()
    result = _runner(db).run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                                 today=TRADE_DATE)
    assert result["status"] == "FAILED"
    assert result["results"]["eod_gates"]["status"] == "FAIL"
    assert "NOT_TRADING_DAY" in str(result["results"]["eod_gates"]["error"])
    assert _downstream_blocked(result["results"], "eod_gates")
    assert not _manifest_exists(db)


def test_missing_market_data_blocks_valuation(db):
    """行情缺失 → 收盘估值失败；不得对账为通过，不生成 manifest。"""
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM daily WHERE ts_code='000001.SZ' AND trade_date=?",
                  (TRADE_DATE,))
        c.commit()
    result = _runner(db).run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                                 today=TRADE_DATE)
    assert result["status"] == "FAILED"
    assert result["results"]["close_valuation"]["status"] == "FAIL"
    assert "NO_VALUATION" in str(result["results"]["close_valuation"]["error"])
    # 估值失败后不得对账为通过
    assert result["results"]["internal_reconciliation"]["status"] == "SKIPPED"
    assert _downstream_blocked(result["results"], "close_valuation")
    with sqlite3.connect(db) as c:
        snap = c.execute(
            "SELECT COUNT(*) FROM pt_daily_snapshot WHERE trade_date=?", (TRADE_DATE,)
        ).fetchone()[0]
        rec = c.execute(
            "SELECT COUNT(*) FROM pt_reconciliation WHERE run_date=?", (TRADE_DATE,)
        ).fetchone()[0]
    assert snap == 0
    assert rec == 0  # 对账从未执行
    assert not _manifest_exists(db)


def test_pending_corporate_action_blocks_gate(db):
    """未处理公司行为 → 门禁阻断全部下游。"""
    from ab_screener.application.corporate_action_service import ingest_dividend

    ingest_dividend(db, ts_code="000001.SZ", ex_date="20260807", cash_div_fen=100,
                    source="tushare")
    result = _runner(db).run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                                 today=TRADE_DATE)
    assert result["status"] == "FAILED"
    assert result["results"]["eod_gates"]["status"] == "FAIL"
    assert "CORPORATE_ACTION_PENDING" in str(result["results"]["eod_gates"]["error"])
    assert _downstream_blocked(result["results"], "eod_gates")
    assert not _manifest_exists(db)


def test_match_interrupted_blocks_downstream(db, monkeypatch):
    """撮合中断 → 撮合步骤失败，下游全部阻断。"""
    def boom(db_path, trade_date, **kwargs):
        raise RuntimeError("MATCH_INTERRUPTED 撮合中断注入")

    monkeypatch.setattr("paper_trading.engine.execute_fills", boom)
    result = _runner(db).run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                                 today=TRADE_DATE)
    assert result["status"] == "FAILED"
    assert result["results"]["match_confirmed_orders"]["status"] == "FAIL"
    assert "MATCH_INTERRUPTED" in str(result["results"]["match_confirmed_orders"]["error"])
    assert result["results"]["close_valuation"]["status"] == "SKIPPED"
    assert _downstream_blocked(result["results"], "match_confirmed_orders")
    assert not _manifest_exists(db)


def test_reconciliation_one_cent_diff_blocks_drafts_and_manifest(db):
    """对账一分钱差异 → 内部对账失败；不得生成草稿或 COMPLETE manifest。"""
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO pt_cash_flow (account_id, kind, amount_fen, balance_fen,"
            " ref_id, occurred_at) VALUES (1,'MANUAL',1,50000000,'manipulate',"
            " '2026-08-07T00:00:00+08:00')",
        )
        c.commit()
    result = _runner(db).run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                                 today=TRADE_DATE)
    assert result["status"] == "FAILED"
    assert result["results"]["internal_reconciliation"]["status"] == "FAIL"
    assert "RECONCILIATION_DIFF" in str(
        result["results"]["internal_reconciliation"]["error"])
    assert result["results"]["outcome_backfill"]["status"] == "SKIPPED"
    assert result["results"]["generate_drafts"]["status"] == "SKIPPED"
    assert result["results"]["daily_manifest"]["status"] == "SKIPPED"
    with sqlite3.connect(db) as c:
        phase = c.execute(
            "SELECT phase FROM pt_cycle WHERE run_date=?", (TRADE_DATE,),
        ).fetchone()[0]
    assert phase == "RECONCILE"
    assert not _manifest_exists(db)


def test_audit_write_failure_fails_run(db):
    """审计写失败 → fail-closed：run FAILED，不产生 COMPLETE 证据。"""
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE audit_events")
        c.commit()
    result = _runner(db).run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                                 today=TRADE_DATE)
    assert result["status"] == "FAILED"
    assert "audit" in str(result.get("error", "")).lower() or \
        "审计" in str(result.get("error", ""))
    with sqlite3.connect(db) as c:
        run_status = c.execute(
            "SELECT status FROM dag_runs WHERE trade_date=?", (TRADE_DATE,),
        ).fetchone()[0]
    assert run_status == "FAILED"
    assert not _manifest_exists(db)


def test_process_restart_resumes_same_attempt(db):
    """进程重启：RUNNING attempt 同号覆盖重试，不留永久 RUNNING；租约可续。"""
    lease_id = __import__(
        "ab_screener.data.scheduler_repository", fromlist=["lease_id_for"]
    ).lease_id_for(TRADE_DATE, "ACCOUNT", "1")
    with sqlite3.connect(db) as c:
        assert acquire_lease(c, lease_id=lease_id, holder="crash-runner",
                             trade_date=TRADE_DATE, ttl_seconds=300) is True
        run_id = start_run(c, trade_date=TRADE_DATE)
        record_step_attempt(
            c, run_id=run_id, trade_date=TRADE_DATE, step_name="eod_gates",
            scope_type="ACCOUNT", scope_id="1", input_hash="default",
            attempt=1, status="RUNNING",
        )
    # 重启（同 holder 续租）+ 崩溃遗留 RUNNING attempt 覆盖重试
    result = _runner(db, holder="crash-runner").run_day(
        TRADE_DATE, scope_type="ACCOUNT", scope_id="1", today=TRADE_DATE,
    )
    assert result["status"] == "COMPLETED", result
    with sqlite3.connect(db) as c:
        running = c.execute(
            "SELECT COUNT(*) FROM dag_step_runs WHERE status='RUNNING'"
        ).fetchone()[0]
        run_status = c.execute(
            "SELECT status FROM dag_runs WHERE trade_date=?", (TRADE_DATE,),
        ).fetchone()[0]
        attempts = c.execute(
            "SELECT attempt, status FROM dag_step_runs WHERE step_name='eod_gates'"
            " ORDER BY attempt"
        ).fetchall()
        lease = c.execute("SELECT COUNT(*) FROM dag_leases").fetchone()[0]
    assert running == 0
    assert run_status == "COMPLETED"
    assert [(r[0], r[1]) for r in attempts] == [(1, "SUCCESS")]
    assert lease == 0
