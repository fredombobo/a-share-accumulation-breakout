"""upgrade system 阻断项回归：Parquet 指纹/input_hash/任务状态机。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ab_screener.application.scan_jobs import (
    CANCELLED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    ScanJobStore,
)
from ab_screener.data.migrations_v2 import migrate_v9_governance
from ab_screener.data.parquet_cache import load_daily_cached
from ab_screener.data.repository import MarketRepository, input_hash_for_scan
from ab_screener.domain.costs import COMMISSION_MIN, NOTIONAL, commission_for, simulate_round_trip, size_buy
from ab_screener.domain.errors import FailClosedError
from ab_screener.domain.profile import default_profile
from ab_screener.domain.research_gate import assert_no_edge_claim, can_promote_profile
from prefilter_fast import volume_breakout_candidates


def _init_job_db(db: Path) -> None:
    import sqlite3

    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, name TEXT, checksum TEXT, applied_at TEXT)"
    )
    migrate_v9_governance(c)
    c.execute("INSERT INTO schema_version VALUES (9,'M009','x','2026-01-01')")
    c.commit()
    c.close()


class UpgradeMigrationsTest(unittest.TestCase):
    def test_v9_tables(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            import sqlite3

            c = sqlite3.connect(db)
            c.execute(
                "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, name TEXT, checksum TEXT, applied_at TEXT)"
            )
            c.execute("INSERT INTO schema_version VALUES (8,'m8','x','2026-01-01')")
            c.commit()
            c.close()
            from ab_screener.data.migrations_v2 import run_v2_migrations

            ver = run_v2_migrations(db)
            self.assertGreaterEqual(ver, 9)


class CostEngineTest(unittest.TestCase):
    def test_lot_and_min_commission(self):
        self.assertEqual(size_buy(15.0, NOTIONAL) % 100, 0)
        self.assertGreaterEqual(commission_for(1000), COMMISSION_MIN)

    def test_stop_before_target(self):
        f = simulate_round_trip(
            entry_open=10.0, entry_high=10.2, entry_low=9.9, entry_vol=10000, entry_pre_close=10.0,
            exit_open=10.0, exit_high=11.2, exit_low=9.4, exit_vol=10000, exit_pre_close=10.0,
            stop_price=9.5, target_price=11.0, exit_day_low=9.4, exit_day_high=11.2,
        )
        self.assertTrue(f.filled)
        self.assertLess(f.price, 11.0)

    def test_limit_up_no_buy(self):
        f = simulate_round_trip(
            entry_open=11.0, entry_high=11.0, entry_low=11.0, entry_vol=10000, entry_pre_close=10.0,
            exit_open=11.0, exit_high=11.0, exit_low=11.0, exit_vol=10000, exit_pre_close=10.0,
        )
        self.assertFalse(f.filled)

    def test_candidate_trade_is_repriced_with_costs(self):
        from ab_screener.research.cost_adjustment import cost_adjusted_trade
        from trade_sim import simulate_trade

        bars = pd.DataFrame(
            {
                "open": [10.0, 10.0, 11.0],
                "high": [10.1, 10.2, 11.2],
                "low": [9.9, 9.8, 10.8],
                "close": [10.0, 10.0, 11.0],
                "pre_close": [9.9, 10.0, 10.0],
                "vol": [10_000.0, 10_000.0, 10_000.0],
            }
        )
        sim = simulate_trade(
            bars,
            entry_i=0,
            mode="fixed",
            params={"stop_pct": 0.07, "target_pct": 0.10, "max_hold": 3},
        )
        fill = cost_adjusted_trade(bars, sim)
        self.assertTrue(fill["filled"])
        self.assertGreater(fill["commission"], 0)
        self.assertLess(fill["net_return"], sim["ret"])

    def test_promotion_metrics_never_fall_back_to_gross(self):
        from ab_screener.research.cost_adjustment import promotion_metrics

        metrics = promotion_metrics(
            {
                "oos_profit_factor": 9.0,
                "oos_win_rate": 0.9,
                "oos_max_drawdown": 0.01,
                "oos_net_profit_factor": 0.7,
                "oos_net_win_rate": 0.2,
                "oos_net_max_drawdown": 0.4,
            }
        )
        self.assertEqual(metrics, {"profit_factor": 0.7, "win_rate": 0.2, "max_drawdown": 0.4})


class ResearchGateTest(unittest.TestCase):
    def test_fail_closed(self):
        with self.assertRaises(FailClosedError):
            assert_no_edge_claim("已验证 edge", research_mode="degraded")
        r = can_promote_profile(
            research_mode="degraded", oos_net_pf=1.5, oos_max_dd=0.1,
            oos_win_rate=0.4, beats_baseline=True,
        )
        self.assertFalse(r["promotable"])


class MarketRegimeFreshnessTest(unittest.TestCase):
    def test_stale_index_fails_closed_without_network(self):
        from market_regime import detect_regime

        class FakeStore:
            @staticmethod
            def load_daily(ts_codes=None):
                return pd.DataFrame(
                    {
                        "ts_code": ["000300.SH"] * 30,
                        "trade_date": [f"202607{i:02d}" for i in range(1, 31)],
                        "close": [4000.0 + i for i in range(30)],
                    }
                )

            @staticmethod
            def max_trade_date(table):
                return "20260807"

        regime = detect_regime(store=FakeStore(), allow_network=False)
        self.assertFalse(regime.allow_new_entries)
        self.assertEqual(regime.regime, "defense")
        self.assertIn("过期", regime.label)
        self.assertEqual(regime.as_of, "20260730")

    def test_real_gate_benchmark_check_requires_latest_date(self):
        from paper_trading.real_data_gate import _benchmark_is_current

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "m.db"
            import sqlite3

            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE daily(ts_code TEXT, trade_date TEXT)")
            conn.execute("INSERT INTO daily VALUES ('000300.SH','20260806')")
            conn.commit()
            conn.close()
            ok, actual = _benchmark_is_current(db, "20260807")
            self.assertFalse(ok)
            self.assertEqual(actual, "20260806")


class PrefilterVectorTest(unittest.TestCase):
    def test_vector_prefilter_basic(self):
        rows = []
        for code, last_vol in [("000001.SZ", 500.0), ("000002.SZ", 50.0)]:
            for i in range(20):
                rows.append({
                    "ts_code": code,
                    "trade_date": f"202601{i + 1:02d}",
                    "close": 10.0 + i * 0.01,
                    "high": 10.5,
                    "vol": 100.0 if i < 19 else last_vol,
                })
        keep = volume_breakout_candidates(pd.DataFrame(rows), ["000001.SZ", "000002.SZ"], vol_ratio_min=1.5)
        self.assertIn("000001.SZ", keep)


class ThemeFillReuseTest(unittest.TestCase):
    def test_precomputed_signal_is_not_detected_twice(self):
        from run_screener import _soft_setup_row

        bars = pd.DataFrame(
            {
                "trade_date": ["20260101", "20260102"],
                "date": ["2026-01-01", "2026-01-02"],
                "close": [10.0, 10.1],
                "high": [10.1, 10.2],
                "low": [9.9, 10.0],
                "vol": [100.0, 120.0],
            }
        )
        meta = pd.Series(
            {
                "name": "测试股份",
                "industry": "计算机",
                "close": 10.1,
                "pe": 20.0,
                "pb": 2.0,
                "total_mv": 1_000_000.0,
                "turnover_rate": 2.0,
                "list_date": "20200101",
            }
        )
        signal = {
            "is_breakout": False,
            "reasons": ["预计算"],
            "box_days": 20,
            "box_amp": 0.10,
            "box_high": 10.2,
            "latest_close": 10.1,
        }
        with patch("run_screener.detect_accumulation_breakout", side_effect=AssertionError("重复检测")):
            row = _soft_setup_row("000001.SZ", bars, meta, None, "AI应用", signal=signal)
        self.assertIsNotNone(row)


class ProfileTest(unittest.TestCase):
    def test_hash_stable(self):
        self.assertEqual(default_profile().config_hash(), default_profile().config_hash())


class InputHashTest(unittest.TestCase):
    def test_different_code_sets_differ(self):
        a = [f"{i:06d}.SZ" for i in range(60)]
        b = [f"{i:06d}.SZ" for i in range(1, 61)]  # 偏移，前 50 与 a 的 1..50 重叠但全集不同
        ha = input_hash_for_scan(as_of="20260801", days=160, codes=a, config_hash="c", dataset_version="d")
        hb = input_hash_for_scan(as_of="20260801", days=160, codes=b, config_hash="c", dataset_version="d")
        self.assertNotEqual(ha, hb)

    def test_scan_result_hash_ignores_persistence_timestamp(self):
        from ab_screener.application.scan_audit import hash_scan_result

        summary = {"count_a": 1, "count_b": 0, "hits": 1, "total_candidates": 5}
        first = [{"ts_code": "000001.SZ", "total_score": 90.0, "created_at": "2026-08-08T10:00:00"}]
        second = [{"ts_code": "000001.SZ", "total_score": 90.0, "created_at": "2026-08-08T11:00:00"}]
        self.assertEqual(hash_scan_result(summary, first), hash_scan_result(summary, second))


class ParquetStaleTest(unittest.TestCase):
    def test_mid_date_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "m.db"
            import sqlite3

            c = sqlite3.connect(db)
            c.execute(
                "CREATE TABLE daily(ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, "
                "close REAL, pre_close REAL, pct_chg REAL, vol REAL, amount REAL, PRIMARY KEY(ts_code, trade_date))"
            )
            c.execute(
                "CREATE TABLE dataset_partitions(dataset TEXT, trade_date TEXT, row_count INTEGER, "
                "content_sha256 TEXT, revision INTEGER, ingested_at TEXT, PRIMARY KEY(dataset, trade_date))"
            )
            c.execute(
                "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, name TEXT, checksum TEXT, applied_at TEXT)"
            )
            c.execute("INSERT INTO schema_version VALUES (9,'M009','x','2026-01-01')")
            for d, px in [("20260101", 10.0), ("20260102", 20.0), ("20260103", 30.0)]:
                c.execute(
                    "INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("000001.SZ", d, px, px, px, px, px, 0, 100, 1000),
                )
            c.commit()
            c.close()
            from ab_screener.data.migrations_v2 import run_v2_migrations

            self.assertGreaterEqual(run_v2_migrations(db), 10)
            repo = MarketRepository(db)
            cache = Path(td) / "pq"
            df1, m1 = load_daily_cached(repo, start="20260101", end="20260103", cache_dir=cache)
            self.assertFalse(m1["cache_hit"])
            self.assertEqual(float(df1.loc[df1.trade_date == "20260102", "close"].iloc[0]), 20.0)
            # 改中间日
            c = sqlite3.connect(db)
            c.execute("UPDATE daily SET close=99, open=99, high=99, low=99 WHERE trade_date='20260102'")
            c.commit()
            c.close()
            df2, m2 = load_daily_cached(repo, start="20260101", end="20260103", cache_dir=cache)
            self.assertFalse(m2["cache_hit"])  # 指纹变了必须 miss
            self.assertEqual(float(df2.loc[df2.trade_date == "20260102", "close"].iloc[0]), 99.0)
            self.assertNotEqual(m1["key"], m2["key"])


class ScanJobsStateMachineTest(unittest.TestCase):
    def test_orphaned_success_is_preserved_as_an_invalid_audit(self):
        from ab_screener.application.scan_audit import record_orphaned_successes

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "j.db"
            _init_job_db(db)
            store = ScanJobStore(db)
            tid = store.create(top_n=10, days=60)
            store.claim_next("w1")
            self.assertTrue(store.finish(tid, status=SUCCEEDED, run_id=tid))

            repaired = record_orphaned_successes(db, code_version="repair-build")

            self.assertEqual(repaired, [tid])
            import sqlite3

            conn = sqlite3.connect(db)
            try:
                run = conn.execute(
                    "SELECT status, input_hash, result_hash FROM scan_runs WHERE run_id=?",
                    (tid,),
                ).fetchone()
                self.assertEqual(run[0], "INVALID_ORPHAN")
                self.assertEqual(len(run[1]), 64)
                self.assertEqual(len(run[2]), 64)
            finally:
                conn.close()

    def test_success_and_audit_run_are_committed_together(self):
        from ab_screener.application.scan_audit import complete_scan_run

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "j.db"
            _init_job_db(db)
            store = ScanJobStore(db)
            tid = store.create(top_n=10, days=60)
            store.claim_next("w1")

            completed = complete_scan_run(
                db,
                run_id=tid,
                task_id=tid,
                as_of="20260807",
                days=60,
                result={"total_candidates": 12, "hits": 3},
                count_a=1,
                count_b=2,
                strategy_snapshot={"profile_id": "test"},
                config_hash="c" * 64,
                code_version="test-build",
                research_mode="full",
            )

            self.assertTrue(completed)
            self.assertEqual(store.get(tid)["status"], SUCCEEDED)
            import sqlite3

            conn = sqlite3.connect(db)
            try:
                run = conn.execute(
                    "SELECT input_hash, result_hash, dataset_version, status FROM scan_runs WHERE run_id=?",
                    (tid,),
                ).fetchone()
                self.assertIsNotNone(run)
                self.assertEqual(run[3], SUCCEEDED)
                self.assertEqual(len(run[0]), 64)
                self.assertEqual(len(run[1]), 64)
                self.assertEqual(len(run[2]), 64)
            finally:
                conn.close()

    def test_cancelled_job_cannot_gain_a_success_audit(self):
        from ab_screener.application.scan_audit import complete_scan_run

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "j.db"
            _init_job_db(db)
            store = ScanJobStore(db)
            tid = store.create(top_n=10, days=60)
            store.claim_next("w1")
            store.request_cancel(tid)

            completed = complete_scan_run(
                db,
                run_id=tid,
                task_id=tid,
                as_of="20260807",
                days=60,
                result={},
                count_a=0,
                count_b=0,
                strategy_snapshot={},
                config_hash="c" * 64,
                code_version="test-build",
                research_mode="full",
            )

            self.assertFalse(completed)
            self.assertEqual(store.get(tid)["status"], CANCELLED)
            import sqlite3

            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0], 0)
            finally:
                conn.close()

    def test_cancel_blocks_success(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "j.db"
            _init_job_db(db)
            store = ScanJobStore(db)
            tid = store.create(top_n=10, days=60)
            job = store.claim_next("w1")
            self.assertEqual(job["status"], RUNNING)
            store.request_cancel(tid)
            # 试图 SUCCEEDED 必须被拒绝或转为 CANCELLED
            store.finish(tid, status=SUCCEEDED, run_id="r1")
            final = store.get(tid)
            self.assertEqual(final["status"], CANCELLED)
            # 终态不可再改
            self.assertFalse(store.finish(tid, status=SUCCEEDED, run_id="r2"))
            self.assertEqual(store.get(tid)["status"], CANCELLED)

    def test_requeue_respects_stale_seconds(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "j.db"
            _init_job_db(db)
            store = ScanJobStore(db)
            tid = store.create(top_n=5, days=30)
            store.claim_next("w1")
            # 刚领取：不应 requeue
            n = store.requeue_stale(stale_seconds=120)
            self.assertEqual(n, 0)
            self.assertEqual(store.get(tid)["status"], RUNNING)
            # 伪造过期心跳
            import sqlite3
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo

            old = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(seconds=300)).isoformat()
            c = sqlite3.connect(db)
            c.execute("UPDATE scan_jobs SET heartbeat_at=?, started_at=? WHERE task_id=?", (old, old, tid))
            c.commit()
            c.close()
            n2 = store.requeue_stale(stale_seconds=120)
            self.assertEqual(n2, 1)
            self.assertEqual(store.get(tid)["status"], QUEUED)


class NoPickleReadTest(unittest.TestCase):
    def test_load_market_data_no_pickle(self):
        import inspect

        import run_screener

        self.assertNotIn("read_pickle", inspect.getsource(run_screener.load_market_data))


if __name__ == "__main__":
    unittest.main()
