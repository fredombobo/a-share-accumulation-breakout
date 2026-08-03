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
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from local_store import LocalStore


class UpsertPrimaryKeyOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = LocalStore(db_path=Path(self._tmp.name) / "test.db")

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


if __name__ == "__main__":
    unittest.main()
