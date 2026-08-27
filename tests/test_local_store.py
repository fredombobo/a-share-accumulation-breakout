"""
local_store._upsert 主键-only df 不抛错 —— 离线骨架测试（B14）
============================================================
注意：local_store.py 正被其他任务并发修改，本文件针对「最终接口」编写，
      **不要执行**（仅 py_compile；最终集成验证由主流程统一跑）。
说明：LocalStore(db_path=...) 原生支持指定 DB 路径，故直接传入临时文件，
      无需 monkeypatch 模块级常量。若最终 _upsert 签名变动，本测试需同步。
集成阶段执行方式：
    C:\\Python314\\python.exe -m unittest tests.test_local_store
"""
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ab_screener.data.migration_registry import apply_pending
from local_store import (
    LocalStore,
    _bounded_fetch_dates,
    _completed_open_dates,
    _missing_dates_in_lookback,
    _reconciliation_targets,
    _sync_benchmark_index,
)


class BoundedHistoryFetchTest(unittest.TestCase):
    def test_fetches_dates_concurrently_with_a_hard_bound(self) -> None:
        lock = threading.Lock()
        active = 0
        peak = 0

        def fetch_one(value: str) -> tuple[str, str]:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return value, value

        rows = _bounded_fetch_dates([str(i) for i in range(8)], fetch_one, workers=3)
        self.assertEqual({key for key, _ in rows}, {str(i) for i in range(8)})
        self.assertGreaterEqual(peak, 2)
        self.assertLessEqual(peak, 3)

    def test_moneyflow_gap_check_is_limited_to_requested_window(self) -> None:
        dates = ["20260101", "20260102", "20260103", "20260104"]
        self.assertEqual(
            _missing_dates_in_lookback(dates, {"20260104"}, lookback=2),
            ["20260103"],
        )

    def test_current_open_day_is_not_complete_before_close_cutoff(self) -> None:
        dates = ["20260826", "20260827"]
        tz = ZoneInfo("Asia/Shanghai")
        self.assertEqual(
            _completed_open_dates(
                dates,
                now=datetime(2026, 8, 27, 16, 14, tzinfo=tz),
            ),
            ["20260826"],
        )
        self.assertEqual(
            _completed_open_dates(
                dates,
                now=datetime(2026, 8, 27, 16, 15, tzinfo=tz),
            ),
            dates,
        )

    def test_reconciliation_includes_gaps_and_last_five_completed_days(self) -> None:
        dates = [f"202608{day:02d}" for day in range(1, 8)]
        missing, targets = _reconciliation_targets(
            dates,
            set(dates) - {"20260802"},
            lookback=7,
            force=False,
        )
        self.assertEqual(missing, ["20260802"])
        self.assertEqual(
            targets,
            [
                "20260802",
                "20260803",
                "20260804",
                "20260805",
                "20260806",
                "20260807",
            ],
        )


class UpsertPrimaryKeyOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = LocalStore(db_path=Path(self._tmp.name) / "test.db")
        with closing(sqlite3.connect(self.store.db_path)) as conn:
            apply_pending(conn)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_upsert_pk_only_daily(self) -> None:
        """只含主键列 (ts_code, trade_date) 的 df，_upsert 不应抛错且可回读。"""
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"],
            "trade_date": ["20260803", "20260803"],
        })
        n = self.store._upsert("daily", df)
        self.assertEqual(n, 2)
        loaded = self.store.load_daily(ts_codes=["000001.SZ", "600000.SH"])
        self.assertEqual(len(loaded), 2)

    def test_upsert_pk_only_scan_result(self) -> None:
        """scan_result 主键为 (trade_date, ts_code)，同样不应抛错。"""
        df = pd.DataFrame({
            "trade_date": ["20260803", "20260803"],
            "ts_code": ["000001.SZ", "600000.SH"],
        })
        n = self.store._upsert("scan_result", df)
        self.assertEqual(n, 2)

    def test_upsert_empty_df_returns_zero(self) -> None:
        """空 df 直接返回 0，不触碰 SQL。"""
        self.assertEqual(self.store._upsert("daily", pd.DataFrame()), 0)

    def test_upsert_pk_only_repeated_is_idempotent(self) -> None:
        """主键-only 重复写入应幂等（不产生重复行）。"""
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260803"]})
        self.store._upsert("daily", df)
        self.store._upsert("daily", df)
        loaded = self.store.load_daily(ts_codes=["000001.SZ"])
        self.assertEqual(len(loaded), 1)

    def test_benchmark_index_is_incremental_and_gets_point_in_time_metadata(self) -> None:
        self.store.upsert_daily(pd.DataFrame({
            "ts_code": ["000300.SH"],
            "trade_date": ["20260807"],
            "open": [10.0], "high": [10.2], "low": [9.9], "close": [10.1],
            "vol": [100.0], "amount": [1000.0],
        }))

        class FakePro:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            def index_daily(
                self, *, ts_code: str, start_date: str, end_date: str
            ) -> pd.DataFrame:
                self.calls.append((ts_code, start_date, end_date))
                return pd.DataFrame({
                    "ts_code": ["000300.SH", "000300.SH", "000300.SH"],
                    "trade_date": ["20260807", "20260810", "20260811"],
                    "open": [10.0, 10.1, 10.2], "high": [10.2, 10.3, 10.4],
                    "low": [9.9, 10.0, 10.1], "close": [10.1, 10.2, 10.3],
                    "vol": [100.0, 100.0, 100.0],
                    "amount": [1000.0, 1000.0, 1000.0],
                })

        pro = FakePro()
        first = _sync_benchmark_index(
            self.store, pro, ["20260807", "20260810", "20260811"]
        )
        second = _sync_benchmark_index(
            self.store, pro, ["20260807", "20260810", "20260811"]
        )

        rows = self.store.load_daily(ts_codes=["000300.SH"])
        latest = rows.loc[rows["trade_date"] == "20260811"].iloc[0]
        self.assertEqual(first["dates"], ["20260810", "20260811"])
        self.assertEqual(first["checked_dates"], ["20260807", "20260810", "20260811"])
        self.assertEqual(first["failed_dates"], [])
        self.assertEqual(first["rows"], 3)
        self.assertEqual(second["dates"], [])
        self.assertEqual(second["checked_dates"], ["20260807", "20260810", "20260811"])
        self.assertEqual(second["rows"], 0)
        self.assertEqual(
            pro.calls,
            [
                ("000300.SH", "20260807", "20260811"),
                ("000300.SH", "20260807", "20260811"),
            ],
        )
        self.assertEqual(latest["source"], "tushare_index_daily")
        self.assertTrue(str(latest["available_at"]).endswith("+08:00"))

    def test_benchmark_index_retries_a_transient_provider_disconnect(self) -> None:
        class FlakyPro:
            def __init__(self) -> None:
                self.calls = 0

            def index_daily(
                self, *, ts_code: str, start_date: str, end_date: str
            ) -> pd.DataFrame:
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("temporary reset")
                return pd.DataFrame({
                    "ts_code": [ts_code],
                    "trade_date": [start_date],
                    "open": [10.0],
                    "high": [10.1],
                    "low": [9.9],
                    "close": [10.0],
                    "vol": [100.0],
                    "amount": [1000.0],
                })

        pro = FlakyPro()
        result = _sync_benchmark_index(
            self.store,
            pro,
            ["20260811"],
            max_attempts=2,
            retry_delay=0,
        )

        self.assertEqual(pro.calls, 2)
        self.assertEqual(result["dates"], ["20260811"])
        self.assertEqual(result["checked_dates"], ["20260811"])
        self.assertEqual(result["failed_dates"], [])
        self.assertEqual(result["rows"], 1)


if __name__ == "__main__":
    unittest.main()
