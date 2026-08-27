"""封闭循环：生产 EOD factory 在独立副本库上的完整日终、重放幂等与账本不变。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ab_screener.application import signal_outcomes
from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.scheduler_repository import record_step_attempt, start_run
from ab_screener.operations.dag import (
    _close_quote_micro,
    _entry_price_micro,
    _step_outcome_backfill,
    build_eod_dag,
)
from ab_screener.operations.scheduler import SchedulerRunner
from paper_trading.account import commit_import, create_account
from paper_trading.migrations import run_migrations
from paper_trading.orders import confirm_order, create_buy_draft
from scripts.run_eod_v2 import EodOperatorError, run_eod
from tests.paper_market_fixture import seed_fresh_neutral_benchmark

TRADE_DATE = "20260807"

# 账本业务表（幂等重放前后不得增加；对账/审计记录表不在此列）
_LEDGER_TABLES = (
    "pt_order", "pt_fill", "pt_cash_flow", "pt_position_lot",
    "pt_signal_snapshot", "pt_daily_snapshot", "signal_observations", "signal_outcomes",
)


def _ledger_counts(db: str) -> dict[str, int]:
    with sqlite3.connect(db) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in _LEDGER_TABLES}


def _build_closed_loop_db(tmp_path: Path) -> str:
    db = str(tmp_path / "stock_data.db")
    from ab_screener.local_store import LocalStore

    LocalStore(db_path=db)
    run_migrations(db)
    with sqlite3.connect(db) as c:
        apply_pending(c)
        c.executemany(
            "INSERT OR IGNORE INTO daily (ts_code, trade_date, open, high, low, close,"
            " vol, amount) VALUES (?,?,?,?,?,?,?,?)",
            [
                ("000001.SZ", "20260805", 10.0, 10.2, 9.8, 10.0, 100000.0, 1000000.0),
                ("000001.SZ", "20260806", 10.0, 10.3, 9.9, 10.2, 120000.0, 1220000.0),
                ("000001.SZ", "20260807", 10.2, 10.5, 10.1, 10.4, 150000.0, 1550000.0),
            ],
        )
        seed_fresh_neutral_benchmark(c)
        c.executemany(
            "INSERT OR REPLACE INTO trade_cal (cal_date, is_open, source, updated_at)"
            " VALUES (?,?,?,?)",
            [("20260805", 1, "tushare", "t"), ("20260806", 1, "tushare", "t"),
             ("20260807", 1, "tushare", "t"), ("20260808", 0, "tushare", "t"),
             ("20260810", 1, "tushare", "t")],
        )
        c.commit()
    create_account(db, 50_000_000)
    pf = tmp_path / "portfolio.json"
    pf.write_text(json.dumps({"positions": [
        {"ts_code": "000001.SZ", "cost": 10.0, "shares": 200,
         "opened_at": "2026-08-01T10:00:00"},
    ]}), encoding="utf-8")
    commit_import(db, str(pf), as_of_date="20260806")
    with sqlite3.connect(db) as c:
        # manifest COMPLETE 必需：scan_runs + 代码/配置身份
        c.execute(
            "INSERT INTO scan_runs (run_id, task_id, as_of, config_hash, git_sha,"
            " dataset_version, input_hash, result_hash, research_mode, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("SR-TEST", "T1", TRADE_DATE, "cfg-test", "test-git-sha", "daily:20260807",
             "in", "res", "prod", "SUCCEEDED", "2026-08-07T16:00:00+08:00"),
        )
        # 当日可交易 A 池信号（20260806 生成，用于买入草稿；tradeable 默认 1）
        c.execute(
            "INSERT OR REPLACE INTO pt_signal_snapshot (trade_date, ts_code, pool,"
            " total_score, suggested_pos_pct, strategy_version, input_hash, available_at)"
            " VALUES ('20260806','000001.SZ','A',80,10,'v1','h','2026-08-06T15:30:00+08:00')",
        )
        c.commit()
    order = create_buy_draft(db, ts_code="000001.SZ", trade_date="20260806",
                             suggested_pos_pct=10.0, input_hash="h", qty=100)
    confirm_order(db, order["order_id"], today="20260806")
    return db


@pytest.fixture()
def closed_loop_db(tmp_path: Path) -> str:
    return _build_closed_loop_db(tmp_path)


def test_closed_loop_full_eod_completes(closed_loop_db):
    dag = build_eod_dag(closed_loop_db, today=TRADE_DATE)
    runner = SchedulerRunner(closed_loop_db, dag, holder="eod-test")
    first = runner.run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                           today=TRADE_DATE)
    assert first["status"] == "COMPLETED", first
    with sqlite3.connect(closed_loop_db) as c:
        manifest = c.execute(
            "SELECT status, manifest_sha256 FROM daily_run_manifests"
            " WHERE trade_date=?", (TRADE_DATE,),
        ).fetchone()
        cycle = c.execute(
            "SELECT phase FROM pt_cycle WHERE run_date=?", (TRADE_DATE,),
        ).fetchone()
        rec = c.execute(
            "SELECT result FROM pt_reconciliation WHERE run_date=?"
            " ORDER BY rec_id DESC LIMIT 1", (TRADE_DATE,),
        ).fetchone()
        fills = c.execute("SELECT COUNT(*) FROM pt_fill").fetchone()[0]
        dag_run = c.execute(
            "SELECT status FROM dag_runs WHERE trade_date=?", (TRADE_DATE,),
        ).fetchone()
        risk = c.execute(
            "SELECT market_version,rule_version,config_version FROM risk_snapshots"
            " WHERE trade_date=?", (TRADE_DATE,),
        ).fetchone()
    assert manifest[0] == "COMPLETE"
    assert cycle[0] == "DONE"
    assert rec[0] == "OK"
    assert fills >= 1
    assert dag_run[0] == "COMPLETED"
    assert risk == ("daily:20260807", "risk-v2", "robust_personal_v2")
    # 审计链有效（DAG_RUN_START / DAG_RUN_FINISHED 等）
    from ab_screener.application.audit_service import verify_audit_chain

    with sqlite3.connect(closed_loop_db) as c:
        check = verify_audit_chain(c)
    assert check["valid"] is True, check["broken"]


def test_closed_loop_replay_idempotent(closed_loop_db):
    """同输入完整重放：账本业务表行数/余额不变，dag_runs 不增加。"""
    dag = build_eod_dag(closed_loop_db, today=TRADE_DATE)
    runner = SchedulerRunner(closed_loop_db, dag, holder="eod-test")
    first = runner.run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                           today=TRADE_DATE)
    assert first["status"] == "COMPLETED", first
    with sqlite3.connect(closed_loop_db) as c:
        snapshot_before = c.execute(
            "SELECT cash_fen, market_value_fen, total_asset_fen FROM pt_daily_snapshot"
            " WHERE account_id=1 AND trade_date=?", (TRADE_DATE,),
        ).fetchone()
    counts_before = _ledger_counts(closed_loop_db)

    second = runner.run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                            today=TRADE_DATE)
    assert second["status"] == "COMPLETED", second
    with sqlite3.connect(closed_loop_db) as c:
        snapshot_after = c.execute(
            "SELECT cash_fen, market_value_fen, total_asset_fen FROM pt_daily_snapshot"
            " WHERE account_id=1 AND trade_date=?", (TRADE_DATE,),
        ).fetchone()
        runs = c.execute(
            "SELECT status FROM dag_runs WHERE trade_date=?", (TRADE_DATE,),
        ).fetchall()
        running = c.execute(
            "SELECT COUNT(*) FROM dag_step_runs WHERE status='RUNNING'"
        ).fetchone()[0]
        leases = c.execute("SELECT COUNT(*) FROM dag_leases").fetchone()[0]
    assert snapshot_after == snapshot_before
    assert _ledger_counts(closed_loop_db) == counts_before
    assert [r[0] for r in runs] == ["COMPLETED"]
    assert running == 0
    assert leases == 0  # 退出释放，无永久租约


def test_closed_loop_input_identity_change_no_ledger_growth(closed_loop_db):
    """输入身份变化会重跑步骤，但业务账本幂等（不重复成交/现金/持仓/草稿）。"""
    dag = build_eod_dag(closed_loop_db, today=TRADE_DATE)
    runner = SchedulerRunner(closed_loop_db, dag, holder="eod-test")
    first = runner.run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                           today=TRADE_DATE)
    assert first["status"] == "COMPLETED"
    counts_before = _ledger_counts(closed_loop_db)

    runner2 = SchedulerRunner(closed_loop_db, dag, holder="eod-test")
    second = runner2.run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                             input_hash="v2-identity", today=TRADE_DATE)
    assert second["status"] == "COMPLETED", second
    # 步骤重跑但账本业务表不增加（业务幂等）
    assert _ledger_counts(closed_loop_db) == counts_before
    with sqlite3.connect(closed_loop_db) as c:
        rows = c.execute(
            "SELECT COUNT(DISTINCT step_run_id) FROM dag_step_runs"
            " WHERE input_hash='v2-identity'"
        ).fetchone()[0]
    assert rows >= 9  # v2 身份下 9 步都有新的 attempt 记录


def test_control_tables_interpretable_after_run(closed_loop_db):
    """dag_runs/dag_step_runs/dag_leases/audit_events 有可解释记录。"""
    dag = build_eod_dag(closed_loop_db, today=TRADE_DATE)
    runner = SchedulerRunner(closed_loop_db, dag, holder="eod-test")
    result = runner.run_day(TRADE_DATE, scope_type="ACCOUNT", scope_id="1",
                            today=TRADE_DATE)
    assert result["status"] == "COMPLETED"
    with sqlite3.connect(closed_loop_db) as c:
        run_row = c.execute("SELECT run_id, trade_date, status FROM dag_runs").fetchone()
        step_rows = c.execute(
            "SELECT step_name, attempt, status FROM dag_step_runs ORDER BY attempt"
        ).fetchall()
        audit_rows = c.execute(
            "SELECT action, correlation_id FROM audit_events ORDER BY occurred_at"
        ).fetchall()
    assert run_row[1] == TRADE_DATE and run_row[2] == "COMPLETED"
    names = [r[0] for r in step_rows]
    for step in ("eod_gates", "release_matured_lots", "match_confirmed_orders",
                 "close_valuation", "risk_pnl_snapshot", "internal_reconciliation",
                 "outcome_backfill", "generate_drafts", "daily_manifest"):
        assert step in names, step
    assert all(r[2] == "SUCCESS" for r in step_rows)
    actions = [r[0] for r in audit_rows]
    assert "DAG_RUN_START" in actions and "DAG_RUN_FINISHED" in actions
    assert {r[1] for r in audit_rows} == {run_row[0]}


def test_resume_after_close_valuation_rebuilds_process_local_context(closed_loop_db):
    """close_valuation 成功后进程崩溃，重启不得因内存 ctx 丢失而失败。"""
    dag = build_eod_dag(closed_loop_db, today=TRADE_DATE)
    ctx = {"db_path": Path(closed_loop_db), "today": TRADE_DATE, "results": {}}
    with sqlite3.connect(closed_loop_db) as conn:
        run_id = start_run(conn, trade_date=TRADE_DATE)
    for step in dag.steps[:4]:
        step_result = step.fn(trade_date=TRADE_DATE, ctx=ctx)
        ctx["results"][step.name] = step_result
        with sqlite3.connect(closed_loop_db) as conn:
            record_step_attempt(
                conn,
                run_id=run_id,
                trade_date=TRADE_DATE,
                step_name=step.name,
                scope_type="ACCOUNT",
                scope_id="1",
                input_hash="default",
                attempt=1,
                status="SUCCESS",
            )

    restarted = SchedulerRunner(closed_loop_db, dag, holder="restarted").run_day(
        TRADE_DATE,
        scope_type="ACCOUNT",
        scope_id="1",
        today=TRADE_DATE,
    )
    assert restarted["status"] == "COMPLETED", restarted
    assert restarted["results"]["close_valuation"]["idempotent"] is True
    assert restarted["results"]["risk_pnl_snapshot"]["status"] == "SUCCESS"


def test_manifest_failure_revokes_cycle_done_projection(closed_loop_db):
    """manifest 缺硬证据时 DAG 和纸面 cycle 都不得显示完成。"""
    with sqlite3.connect(closed_loop_db) as conn:
        conn.execute("DELETE FROM scan_runs")
        conn.commit()
    dag = build_eod_dag(closed_loop_db, today=TRADE_DATE)
    result = SchedulerRunner(closed_loop_db, dag, holder="manifest-fail").run_day(
        TRADE_DATE,
        scope_type="ACCOUNT",
        scope_id="1",
        today=TRADE_DATE,
    )
    assert result["status"] == "FAILED", result
    with sqlite3.connect(closed_loop_db) as conn:
        cycle = conn.execute(
            "SELECT phase, blocked_reason, finished_at FROM pt_cycle WHERE run_date=?",
            (TRADE_DATE,),
        ).fetchone()
    assert cycle[0] == "RECONCILE"
    assert "MANIFEST_NOT_COMPLETE" in cycle[1]
    assert cycle[2] is None


def test_outcome_backfill_uses_signal_date_fill_and_quote_available_at(
    closed_loop_db, monkeypatch
):
    """信号日与成交日分离，成熟行情必须携带真实 available_at。"""
    dag = build_eod_dag(closed_loop_db, today=TRADE_DATE)
    first = SchedulerRunner(closed_loop_db, dag, holder="outcome-seed").run_day(
        TRADE_DATE,
        scope_type="ACCOUNT",
        scope_id="1",
        today=TRADE_DATE,
    )
    assert first["status"] == "COMPLETED", first

    with sqlite3.connect(closed_loop_db) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (cal_date,is_open,source,updated_at)"
            " VALUES (?,1,'tushare','t')",
            [(d,) for d in ("20260811", "20260812", "20260813", "20260814")],
        )
        conn.execute(
            "INSERT INTO daily (ts_code,trade_date,open,high,low,close,vol,amount,"
            " available_at,source,revision,is_legacy)"
            " VALUES ('000001.SZ','20260814',10.8,11.1,10.7,10.1234567,100000,1000000,"
            " '2026-08-14T16:05:00+08:00','tushare',1,0)"
        )
        conn.execute(
            "INSERT INTO signal_observations (observation_id,strategy_definition_id,"
            " strategy_hash,input_hash,snapshot_id,ts_code,signal_date,config_hash,"
            " payload_json,explanation,tradeable,entry_definition_id,observed_at)"
            " VALUES ('OBS-1','accumulation_breakout_v1','s','i','snap','000001.SZ',"
            " '20260806','cfg','{}','test',1,'A_POOL_STRICT_NEXT_OPEN_V1',"
            " '2026-08-06T15:30:00+08:00')"
        )
        conn.commit()

    captured: list[dict] = []

    def capture_backfill(conn, **kwargs):
        captured.append(kwargs)
        return {"status": "MATURED", "idempotent": False, "outcome_id": "O1"}

    monkeypatch.setattr(signal_outcomes, "HORIZONS", (5,))
    monkeypatch.setattr(signal_outcomes, "backfill_horizon_outcome", capture_backfill)
    result = _step_outcome_backfill(
        "20260814",
        ctx={"db_path": Path(closed_loop_db), "results": {}},
    )
    assert result["processed"] == 1
    call = next(item for item in captured if item["observation_id"] == "OBS-1")
    assert call["entry_price_micro"] == _entry_price_micro(
        closed_loop_db, "000001.SZ", "20260806", "20260807"
    )
    assert call["entry_price_micro"] is not None
    assert call["exit_price_micro"] == 10_123_457
    assert call["data_available_at"] == "2026-08-14T16:05:00+08:00"
    assert _close_quote_micro(closed_loop_db, "000001.SZ", "20260814") == (
        10_123_457,
        "2026-08-14T16:05:00+08:00",
    )


def test_eod_operator_closes_audits_and_collects_soak(closed_loop_db, tmp_path: Path):
    payload = run_eod(
        db_path=closed_loop_db,
        trade_date=TRADE_DATE,
        anchor_dir=tmp_path / "anchors",
        signing_key_file=tmp_path / "audit-signing.key",
        soak_dir=tmp_path / "soak",
        initialize_signing_key=True,
        create_daily_backup=False,
        identity_override={
            "code_version": "test-git-sha",
            "db_fingerprint": "test-db",
            "worktree_clean": True,
        },
        resolved_config_override={"resolved_hash": "platform-test"},
    )

    assert payload["status"] == "PASS"
    assert payload["audit"]["chain_valid"] is True
    assert payload["audit"]["anchor_valid"] is True
    assert Path(payload["soak_evidence"]).is_file()
    assert payload["soak"]["count"] == 1


def test_eod_operator_rejects_stale_scan_identity(closed_loop_db, tmp_path: Path):
    with pytest.raises(EodOperatorError, match="扫描不属于当前构建版本"):
        run_eod(
            db_path=closed_loop_db,
            trade_date=TRADE_DATE,
            anchor_dir=tmp_path / "anchors",
            signing_key_file=tmp_path / "audit-signing.key",
            soak_dir=tmp_path / "soak",
            initialize_signing_key=True,
            create_daily_backup=False,
            identity_override={
                "code_version": "other-build",
                "db_fingerprint": "test-db",
                "worktree_clean": True,
            },
            resolved_config_override={"resolved_hash": "platform-test"},
        )
