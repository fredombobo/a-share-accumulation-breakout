"""
portfolio.save_portfolio 原子写后文件完整可读 —— 离线骨架测试（B14）
==================================================================
注意：portfolio.py 正被其他任务并发修改，本文件针对「最终接口」编写，
      **不要执行**（仅 py_compile）。
说明：save/load 均支持 path 参数，故用临时目录直接注入，无需 monkeypatch。
      期望最终接口：save_portfolio 采用「临时文件 + 原子替换」，写后文件必为
      完整合法 JSON（不会出现半截/损坏内容）；本测试以「完整 JSON 可读」为断言。
集成阶段执行方式：
    C:\\Python314\\python.exe -m unittest tests.test_portfolio_atomic
"""
import json
import tempfile
import unittest
from pathlib import Path

from portfolio import load_portfolio, save_portfolio


class SavePortfolioAtomicTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "runtime" / "portfolio.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_then_read_roundtrip(self) -> None:
        data = {"updated_at": None, "positions": [
            {"ts_code": "000001.SZ", "name": "平安银行", "cost": 10.5, "shares": 1000},
        ]}
        save_portfolio(data, self.path)
        self.assertTrue(self.path.exists())
        # 原子写核心断言：文件是完整合法 JSON（无半截内容）
        parsed = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(parsed["positions"]), 1)
        loaded = load_portfolio(self.path)
        self.assertEqual(loaded["positions"][0]["ts_code"], "000001.SZ")
        self.assertEqual(loaded["positions"][0]["cost"], 10.5)

    def test_save_overwrites_previous(self) -> None:
        save_portfolio({"updated_at": None, "positions": []}, self.path)
        save_portfolio({"updated_at": None, "positions": [{"ts_code": "600000.SH"}]}, self.path)
        loaded = load_portfolio(self.path)
        self.assertEqual(len(loaded["positions"]), 1)
        self.assertEqual(loaded["positions"][0]["ts_code"], "600000.SH")

    def test_file_always_complete_after_each_save(self) -> None:
        """连续多次写入后，每次读到的都是完整合法 JSON（原子性逻辑测试）。"""
        for i in range(5):
            save_portfolio(
                {"updated_at": None, "positions": [{"ts_code": f"CODE{i:04d}"}]}, self.path
            )
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["positions"][0]["ts_code"], f"CODE{i:04d}")

    def test_load_missing_returns_default(self) -> None:
        """文件不存在时 load_portfolio 返回默认结构。"""
        data = load_portfolio(self.path)
        self.assertEqual(data, {"updated_at": None, "positions": []})


if __name__ == "__main__":
    unittest.main()
