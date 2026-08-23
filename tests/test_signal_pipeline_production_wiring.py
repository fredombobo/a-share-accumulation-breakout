"""V2R-S 生产接线测试：扫描幂等、EXPERIMENTAL 硬隔离、outcome 时点门。

覆盖启动包第一组 RED 测试：
1. 同一 scan_run/revision 重放两次只产生一个 observation；新 revision 追加且旧记录不变。
2. EXPERIMENTAL 命中后只产生观察记录，A 池、买入草稿和目标仓位均不变化。
4. ret_5/10/20 在对应交易日未完成或行情 available_at 越界时保持 NULL（UNFILLABLE 不填 0）。

全部使用 tmp_path 临时数据库；不读写生产账本。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_registry import apply_pending
from ab_screener.data.signal_repository import get_observation_at
from ab_screener.strategies.contracts import SignalObservation

# 根 test_signals 的合成突破 fixture（确定性、无网络）
from test_signals import make_synthetic


def _bars():
    return make_synthetic(seed=42, flat_days=80)


def _obs(**over) -> SignalObservation:
    base = {
        "strategy_definition_id": "accumulation_breakout_v1",
        "strategy_hash": "sh1", "input_hash": "ih1", "snapshot_id": "snap1",
        "ts_code": "000001.SZ", "signal_date": "20260810", "config_hash": "ch1",
        "payload": {"box_days": 76}, "explanation": "放量突破",
        "tradeable": True, "entry_definition_id": "NEXT_TRADABLE_OPEN_EXECUTION_V1",
    }
    base.update(over)
    return SignalObservation(**base)


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "prod_wiring.db")
    conn = sqlite3.connect(path)
    try:
        apply_pending(conn)
    finally:
        conn.close()
    return path


def _open(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ── RED #1：scan_run 重放幂等 + 新 revision 追加 ──


def test_replay_same_scan_run_produces_single_observation(db_path, monkeypatch):
    from ab_screener.api.routers import legacy_scan

    monkeypatch.setattr(legacy_scan, "_signal_persistence_enabled", lambda: True)
    bars_reader = lambda code: _bars()

    first = legacy_scan.persist_scan_signals(
        db_path, scan_run_id="RUN-001", candidate_codes=["000001.SZ"],
        strategy_version="v1", bars_reader=bars_reader,
    )
    assert first["persisted"] > 0, first

    # 同 scan_run_id + strategy_version + instrument 重放：不产生新 observation
    second = legacy_scan.persist_scan_signals(
        db_path, scan_run_id="RUN-001", candidate_codes=["000001.SZ"],
        strategy_version="v1", bars_reader=bars_reader,
    )
    assert second["persisted"] == 0, second

    conn = _open(db_path)
    try:
        assert _count(conn, "signal_observations") == first["persisted"]
        # 纯观察：管线不写事件/outcome/A 池
        assert _count(conn, "signal_events") == 0
        assert _count(conn, "signal_outcomes") == 0
    finally:
        conn.close()


def test_new_scan_run_revision_appends_and_old_rows_unchanged(db_path, monkeypatch):
    from ab_screener.api.routers import legacy_scan

    monkeypatch.setattr(legacy_scan, "_signal_persistence_enabled", lambda: True)
    bars_reader = lambda code: _bars()

    r1 = legacy_scan.persist_scan_signals(
        db_path, scan_run_id="RUN-001", candidate_codes=["000001.SZ"],
        strategy_version="v1", bars_reader=bars_reader,
    )
    assert r1["persisted"] > 0
    old_oid = r1["saved_observation_ids"][0]
    old = get_observation_at(db_path, old_oid)
    assert old is not None

    # 新 revision（新 scan_run）→ 追加新 observation，不覆盖历史
    r2 = legacy_scan.persist_scan_signals(
        db_path, scan_run_id="RUN-002", candidate_codes=["000001.SZ"],
        strategy_version="v1", bars_reader=bars_reader,
    )
    assert r2["persisted"] > 0
    assert set(r2["saved_observation_ids"]).isdisjoint(r1["saved_observation_ids"])

    conn = _open(db_path)
    try:
        assert _count(conn, "signal_observations") == r1["persisted"] + r2["persisted"]
    finally:
        conn.close()

    cur = get_observation_at(db_path, old_oid)
    assert cur["payload"] == old["payload"]
    assert cur["observed_at"] == old["observed_at"]
    assert cur["strategy_definition_id"] == old["strategy_definition_id"]


def test_signal_persistence_flag_gated_off_is_noop(db_path):
    """生产默认 V2_STRATEGY_REGISTRY_ENABLED=false：persist_scan_signals 为 no-op。"""
    from ab_screener.api.routers import legacy_scan

    result = legacy_scan.persist_scan_signals(
        db_path, scan_run_id="RUN-001", candidate_codes=["000001.SZ"],
        strategy_version="v1", bars_reader=lambda code: _bars(),
    )
    assert result["enabled"] is False
    assert result["persisted"] == 0
    conn = _open(db_path)
    try:
        assert _count(conn, "signal_observations") == 0
    finally:
        conn.close()


def test_read_daily_bars_bounded_by_as_of(tmp_path):
    """生产 bars reader 必须按 as_of 截断（防未来函数：不用扫描日之后的 K 线）。"""
    from ab_screener.api.routers import legacy_scan

    path = str(tmp_path / "daily_asof.db")
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE daily (ts_code TEXT, trade_date TEXT, open REAL,"
            " high REAL, low REAL, close REAL, pre_close REAL, change REAL,"
            " pct_chg REAL, vol REAL, amount REAL)"
        )
        for d in ("20260807", "20260810", "20260811"):
            conn.execute(
                "INSERT INTO daily VALUES ('000001.SZ', ?,"
                " 10.0, 10.5, 9.8, 10.2, NULL, NULL, NULL, 100000, 1e7)",
                (d,),
            )
        conn.commit()
    finally:
        conn.close()

    df = legacy_scan._read_daily_bars(path, "000001.SZ", as_of="20260810")
    assert df is not None
    # 只含 as_of 当日及以前；20260811 的 K 线被截断
    assert list(df["date"]) == ["20260807", "20260810"]
    # 不提供 as_of 时允许全部（显式调用方自担责任；worker 总是传 as_of）
    full = legacy_scan._read_daily_bars(path, "000001.SZ")
    assert len(full["date"]) == 3


# ── RED #2：EXPERIMENTAL 硬隔离（A 池/买入草稿/目标仓位） ──


def test_experimental_hits_never_enter_a_pool(db_path):
    from ab_screener.application.signal_pipeline import (
        a_pool_candidates,
        run_signal_pipeline,
    )

    conn = _open(db_path)
    try:
        result = run_signal_pipeline(
            conn, bars=_bars(), ts_code="000001.SZ",
            snapshot_id="RUN-001", input_hash="ih1",
        )
        assert result["saved_count"] > 0
        # 六插件全部 EXPERIMENTAL：A 池资格硬隔离为 0
        assert result["a_pool_eligible_count"] == 0
        assert result["a_pool_eligible_ids"] == []
        # 闸门复核：对已保存观察再过一次，仍为空
        assert a_pool_candidates(result["saved_observations"]) == []
        # 纯观察写入：不产生事件（无买入草稿/订单）、不写 outcome（无目标仓位变化）
        assert _count(conn, "signal_events") == 0
        assert _count(conn, "signal_outcomes") == 0
    finally:
        conn.close()


def test_a_pool_gate_requires_active_status():
    from ab_screener.application.signal_pipeline import (
        A_POOL_REQUIRED_STATUS,
        is_a_pool_eligible,
    )
    from ab_screener.strategies.contracts import StrategySpec

    assert A_POOL_REQUIRED_STATUS == "ACTIVE_FOR_A_POOL"

    def spec(status: str) -> StrategySpec:
        return StrategySpec(
            strategy_definition_id="demo", version="v1",
            economic_assumption="a", failure_conditions="b",
            pit_test="c", golden_fixture="f", research_status=status,
        )

    assert is_a_pool_eligible(spec("ACTIVE_FOR_A_POOL")) is True
    assert is_a_pool_eligible(spec("EXPERIMENTAL")) is False
    assert is_a_pool_eligible(spec("CANDIDATE")) is False
    assert is_a_pool_eligible(spec("SHADOW")) is False


def test_a_pool_candidates_filters_experimental_only():
    from ab_screener.application.signal_pipeline import a_pool_candidates

    observations = [_obs()]  # 真实插件观察，其策略均为 EXPERIMENTAL
    assert a_pool_candidates(observations) == []


# ── RED #4：outcome 时点（交易日完成 + available_at <= calculation_at） ──


def test_ret_null_when_trading_day_not_complete():
    from ab_screener.application.signal_outcomes import compute_horizon_result

    r = compute_horizon_result(
        horizon_days=5, entry_price_micro=10_000_000, exit_price_micro=10_500_000,
        cost_rate=0.001, benchmark_excess=None,
        maturity_trade_date="20260818",
        last_completed_trade_date="20260817",  # 第 5 交易日尚未完成
        data_available_at="2026-08-18T16:00:00+08:00",
        calculation_at="2026-08-18T08:00:00+08:00",
    )
    assert r["status"] != "MATURED"
    assert r["net_return"] is None


def test_ret_null_when_market_data_available_at_out_of_bounds():
    from ab_screener.application.signal_outcomes import compute_horizon_result

    r = compute_horizon_result(
        horizon_days=5, entry_price_micro=10_000_000, exit_price_micro=10_500_000,
        cost_rate=0.001, benchmark_excess=None,
        maturity_trade_date="20260817",
        last_completed_trade_date="20260817",
        # 行情 available_at 晚于 calculation_at → 越界，禁止回填
        data_available_at="2026-08-18T16:00:00+08:00",
        calculation_at="2026-08-17T16:00:00+08:00",
    )
    assert r["status"] != "MATURED"
    assert r["net_return"] is None


def test_ret_backfilled_when_trading_day_complete_and_pit_ok():
    from ab_screener.application.signal_outcomes import compute_horizon_result

    r = compute_horizon_result(
        horizon_days=5, entry_price_micro=10_000_000, exit_price_micro=10_500_000,
        cost_rate=0.001, benchmark_excess=0.002,
        maturity_trade_date="20260817",
        last_completed_trade_date="20260817",
        data_available_at="2026-08-17T16:00:00+08:00",
        calculation_at="2026-08-17T16:30:00+08:00",
    )
    assert r["status"] == "MATURED"
    assert r["net_return"] == pytest.approx(0.048, abs=1e-8)
    assert r["benchmark_excess"] == 0.002


def test_unfillable_keeps_null_not_zero():
    from ab_screener.application.signal_outcomes import compute_horizon_result

    r = compute_horizon_result(
        horizon_days=5, entry_price_micro=None, exit_price_micro=None,
        cost_rate=0.001, benchmark_excess=None,
        maturity_trade_date="20260817",
        last_completed_trade_date="20260817",
        data_available_at="2026-08-17T16:00:00+08:00",
        calculation_at="2026-08-17T16:30:00+08:00",
    )
    assert r["status"] == "UNFILLABLE"
    assert r["net_return"] is None


def test_backfill_horizon_outcome_wiring_idempotent(db_path):
    """生产接线：compute_horizon_result → record_outcome（修订追加，重放幂等）。"""
    from ab_screener.application.signal_outcomes import backfill_horizon_outcome
    from ab_screener.data.signal_repository import outcomes_at, save_observation

    conn = _open(db_path)
    try:
        oid = save_observation(conn, _obs())

        # 交易日未完成 → PENDING（net_return NULL）
        pending = backfill_horizon_outcome(
            conn, observation_id=oid, horizon_days=5,
            entry_price_micro=10_000_000, cost_rate=0.001,
            maturity_trade_date="20260818",
            last_completed_trade_date="20260817",
            calculation_at="2026-08-18T08:00:00+08:00",
            exit_price_micro=10_500_000,
            data_available_at="2026-08-18T16:00:00+08:00",
        )
        assert pending["status"] == "PENDING"
        assert pending["net_return"] is None

        # 交易日完成 + available_at 合法 → MATURED 回填
        matured = backfill_horizon_outcome(
            conn, observation_id=oid, horizon_days=5,
            entry_price_micro=10_000_000, cost_rate=0.001,
            maturity_trade_date="20260817",
            last_completed_trade_date="20260817",
            calculation_at="2026-08-17T16:30:00+08:00",
            exit_price_micro=10_500_000,
            data_available_at="2026-08-17T16:00:00+08:00",
        )
        assert matured["status"] == "MATURED"
        assert matured["net_return"] == pytest.approx(0.048, abs=1e-8)

        # 重放幂等：同状态同结果不重复追加 revision（不覆盖历史行）
        again = backfill_horizon_outcome(
            conn, observation_id=oid, horizon_days=5,
            entry_price_micro=10_000_000, cost_rate=0.001,
            maturity_trade_date="20260817",
            last_completed_trade_date="20260817",
            calculation_at="2026-08-17T16:30:00+08:00",
            exit_price_micro=10_500_000,
            data_available_at="2026-08-17T16:00:00+08:00",
        )
        assert again["idempotent"] is True
    finally:
        conn.close()

    rows = outcomes_at(db_path, oid)
    matured_rows = [r for r in rows if r["status"] == "MATURED"]
    assert len(matured_rows) == 1
    assert matured_rows[0]["net_return"] == pytest.approx(0.048, abs=1e-8)
    # 历史行未被覆盖：PENDING（rev1）+ MATURED（rev2）两行都在
    assert len(rows) == 2
    assert [r["revision"] for r in rows] == [1, 2]
