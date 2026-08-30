"""标杆量引擎 + 双模式模拟器 单元测试（真值表驱动，离线纯构造数据）"""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PYTHONPATH", None)

from bench_volume import (
    DIST,
    PUSH,
    WASH,
    bench_exit_events,
    classify_holding_day,
    detect_build_seq,
    find_build_seqs,
)
from trade_sim import simulate_trade, summarize


def mk_df(vols, pcts=None, closes=None, opens=None, highs=None, lows=None):
    n = len(vols)
    closes = closes or [10.0] * n
    pcts = pcts or [1.0] * n
    return pd.DataFrame(
        {
            "date": [f"202601{i // 28 + 1:02d}{i % 28 + 1:02d}" for i in range(n)],
            "open": opens or closes,
            "high": highs or [c * 1.01 for c in closes],
            "low": lows or [c * 0.99 for c in closes],
            "close": closes,
            "vol": vols,
            "pct_chg": pcts,
        }
    )


class TestBuildSeq(unittest.TestCase):
    def test_basic_seq_and_bench_lock(self):
        """3 根连续放量阳 → n=3，标杆=倒数第 2 根放量柱(400)"""
        df = mk_df([100] * 5 + [300, 400, 500], pcts=[0.5] * 5 + [3, 4, 5])
        seq = detect_build_seq(df)
        self.assertTrue(seq["found"])
        self.assertEqual(seq["n"], 3)
        self.assertAlmostEqual(seq["bench_vol"], 400.0)

    def test_single_day_build(self):
        """n=1 时标杆=当天量"""
        df = mk_df([100] * 5 + [300] + [100] * 3, pcts=[0.5] * 5 + [3] + [1] * 3)
        seq = detect_build_seq(df)
        self.assertTrue(seq["found"])
        self.assertEqual(seq["n"], 1)
        self.assertAlmostEqual(seq["bench_vol"], 300.0)

    def test_gap_tolerance(self):
        """放量-缩量小阴-放量：断档容忍，序列不断，n=2"""
        df = mk_df([100] * 5 + [300, 80, 350], pcts=[0.5] * 5 + [3, -1, 2.5])
        seq = detect_build_seq(df)
        self.assertTrue(seq["found"])
        self.assertEqual(seq["n"], 2)
        self.assertAlmostEqual(seq["bench_vol"], 300.0)  # n=2 → 倒数第2根=第1根

    def test_double_gap_terminates(self):
        """连续 2 天断档 → 序列终止；之后再放量 → 新序列"""
        df = mk_df([100] * 5 + [300, 80, 90, 320], pcts=[0.5] * 5 + [3, -1, -1, 2.5])
        seqs = find_build_seqs(df)
        self.assertEqual(len(seqs), 2)
        self.assertEqual(seqs[0]["n"], 1)
        self.assertEqual(seqs[1]["n"], 1)

    def test_no_seq(self):
        df = mk_df([100] * 8, pcts=[0.5] * 8)
        self.assertFalse(detect_build_seq(df)["found"])


class TestQuadrant(unittest.TestCase):
    def test_truth_table(self):
        self.assertEqual(classify_holding_day(300, 2.0, 400), PUSH)
        self.assertEqual(classify_holding_day(300, -1.0, 400), WASH)
        self.assertEqual(classify_holding_day(400, 2.0, 400), DIST)  # >= 标杆即出货
        self.assertEqual(classify_holding_day(500, -3.0, 400), DIST)  # 出货不看阴阳


class TestBenchExit(unittest.TestCase):
    def test_second_dist_within_window_exits(self):
        """10 日窗口内 2 次 DIST → bench 出场"""
        vols = [100] * 11 + [300, 450, 300, 450]
        df = mk_df(vols, pcts=[1.0] * len(vols))
        ev = bench_exit_events(df, entry_i=10, bench_vol=400, exit_window=10, strong_reset=3, max_hold=30)
        self.assertEqual(ev["exit_type"], "bench")
        self.assertEqual(ev["exit_j"], 14)

    def test_strong_days_reset_counter(self):
        """DIST 之间夹 ≥3 根强势日 → 计数清零，需重新累计"""
        vols = [100] * 11 + [450, 100, 100, 100, 450, 450]
        df = mk_df(vols, pcts=[1.0] * len(vols))
        ev = bench_exit_events(df, entry_i=10, bench_vol=400, exit_window=10, strong_reset=3, max_hold=30)
        self.assertEqual(ev["exit_type"], "bench")
        self.assertEqual(ev["exit_j"], 16)  # 第1次(11)被清零，15/16 重新累计两次

    def test_window_expiry_recounters(self):
        """禁用清零时，两次 DIST 间隔 > 窗口 → 重新计数"""
        vols = [100] * 11 + [450] + [300] * 11 + [450, 450]
        df = mk_df(vols, pcts=[1.0] * len(vols))
        ev = bench_exit_events(df, entry_i=10, bench_vol=400, exit_window=10, strong_reset=99, max_hold=30)
        self.assertEqual(ev["exit_type"], "bench")
        self.assertEqual(ev["exit_j"], 24)  # 11→23 超窗重新计数，23/24 两次

    def test_no_dist_timeout(self):
        """全程无出货 → 超时强平"""
        vols = [100] * 45
        df = mk_df(vols, pcts=[1.0] * 45)
        ev = bench_exit_events(df, entry_i=10, bench_vol=400, max_hold=30)
        self.assertEqual(ev["exit_type"], "time")
        self.assertEqual(ev["exit_j"], 40)


class TestTradeSimFixed(unittest.TestCase):
    """fixed 模式必须与旧 _simulate_trade 语义一致（回归基线）"""

    def _bars(self, lows, highs, closes, opens=None):
        n = len(closes)
        return pd.DataFrame(
            {
                "open": opens or closes,
                "high": highs,
                "low": lows,
                "close": closes,
                "vol": [100] * n,
            }
        )

    def test_stop_first(self):
        bars = self._bars(lows=[9.9] * 7 + [9.2], highs=[10.5] * 8, closes=[10.0] * 8, opens=[10.0] * 8)
        r = simulate_trade(
            bars, entry_i=5, mode="fixed", params={"stop_pct": 0.07, "target_pct": 0.12, "max_hold": 15}
        )
        self.assertEqual(r["exit"], "stop")
        self.assertAlmostEqual(r["ret"], -0.07, places=4)

    def test_target_hit(self):
        bars = self._bars(lows=[9.9] * 8, highs=[10.5] * 7 + [11.3], closes=[10.0] * 8, opens=[10.0] * 8)
        r = simulate_trade(
            bars, entry_i=5, mode="fixed", params={"stop_pct": 0.07, "target_pct": 0.12, "max_hold": 15}
        )
        self.assertEqual(r["exit"], "target")
        self.assertAlmostEqual(r["ret"], 0.12, places=4)

    def test_time_exit(self):
        bars = self._bars(lows=[9.9] * 8, highs=[10.5] * 8, closes=[10.0] * 8, opens=[10.0] * 8)
        r = simulate_trade(
            bars, entry_i=5, mode="fixed", params={"stop_pct": 0.07, "target_pct": 0.12, "max_hold": 3}
        )
        self.assertEqual(r["exit"], "time")
        self.assertAlmostEqual(r["ret"], 0.0, places=4)

    def test_entry_day_stop_is_not_sellable_until_next_trade_day(self):
        bars = self._bars(
            lows=[9.9] * 6 + [9.0, 9.9],
            highs=[10.5] * 8,
            closes=[10.0] * 8,
            opens=[10.0] * 8,
        )
        r = simulate_trade(
            bars,
            entry_i=5,
            mode="fixed",
            params={"stop_pct": 0.07, "target_pct": 0.12, "max_hold": 3},
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["exit"], "time")
        self.assertEqual(r["entry_index"], 6)
        self.assertEqual(r["exit_index"], 7)

    def test_window_with_only_entry_bar_is_not_a_completed_trade(self):
        bars = self._bars(
            lows=[9.9] * 7,
            highs=[10.5] * 7,
            closes=[10.0] * 7,
            opens=[10.0] * 7,
        )
        bars["pct_chg"] = 0.0
        fixed = simulate_trade(
            bars,
            entry_i=5,
            mode="fixed",
            params={"stop_pct": 0.07, "target_pct": 0.12, "max_hold": 3},
        )
        bench = simulate_trade(
            bars,
            entry_i=5,
            mode="bench",
            params={"bench_vol": 100, "stop_pct": 0.07, "max_hold": 3},
        )
        self.assertFalse(fixed["ok"])
        self.assertFalse(bench["ok"])
        self.assertEqual(bench["reason"], "NO_T1_EXIT_BAR")


class TestTradeSimBench(unittest.TestCase):
    def test_target_is_real_t1_bench_exit(self):
        n = 20
        highs = [10.1] * n
        highs[12] = 11.3
        bars = pd.DataFrame(
            {
                "open": [10.0] * n,
                "high": highs,
                "low": [9.9] * n,
                "close": [10.0] * n,
                "vol": [100] * n,
                "pct_chg": [0.0] * n,
            }
        )
        result = simulate_trade(
            bars,
            entry_i=10,
            mode="bench",
            params={"bench_vol": 400, "stop_pct": 0.07, "target_pct": 0.12, "max_hold": 8},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["entry_index"], 11)
        self.assertEqual(result["exit_index"], 12)
        self.assertEqual(result["exit"], "target")
        self.assertAlmostEqual(result["ret"], 0.12, places=4)

    def test_stop_has_priority_when_stop_and_target_touch_same_day(self):
        n = 20
        highs = [10.1] * n
        lows = [9.9] * n
        highs[12] = 11.3
        lows[12] = 9.2
        bars = pd.DataFrame(
            {
                "open": [10.0] * n,
                "high": highs,
                "low": lows,
                "close": [10.0] * n,
                "vol": [100] * n,
                "pct_chg": [0.0] * n,
            }
        )
        result = simulate_trade(
            bars,
            entry_i=10,
            mode="bench",
            params={"bench_vol": 400, "stop_pct": 0.07, "target_pct": 0.12, "max_hold": 8},
        )
        self.assertEqual(result["exit"], "stop")
        self.assertAlmostEqual(result["ret"], -0.07, places=4)

    def test_bench_exit_next_open(self):
        """二次出货确认后次日开盘卖出"""
        n = 20
        vols = [100] * 11 + [300, 450, 300, 450] + [100] * (n - 15)
        opens = [10.0] * n
        opens[15] = 10.8  # bench 出场次日开盘
        closes = [10.0] * n
        closes[15] = 10.8
        bars = pd.DataFrame(
            {
                "open": opens,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "vol": vols,
                "pct_chg": [1.0] * n,
            }
        )
        r = simulate_trade(
            bars, entry_i=10, mode="bench", params={"bench_vol": 400, "stop_pct": 0.07, "max_hold": 30}
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["exit"], "bench")
        self.assertAlmostEqual(r["exit_price"], 10.8, places=4)
        self.assertAlmostEqual(r["ret"], 0.08, places=4)

    def test_stop_overrides_bench(self):
        """DIST 日若触发止损，stop 优先（保守序）"""
        n = 20
        vols = [100] * 11 + [300, 450] + [100] * (n - 13)
        closes = [10.0] * n
        lows = [c * 0.99 for c in closes]
        lows[12] = 9.2  # j=12 跌破 -7% 止损（入场 10.0）
        bars = pd.DataFrame(
            {
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": lows,
                "close": closes,
                "vol": vols,
                "pct_chg": [1.0] * n,
            }
        )
        r = simulate_trade(
            bars, entry_i=10, mode="bench", params={"bench_vol": 400, "stop_pct": 0.07, "max_hold": 30}
        )
        self.assertEqual(r["exit"], "stop")
        self.assertAlmostEqual(r["ret"], -0.07, places=4)


class TestSummarize(unittest.TestCase):
    def test_metrics(self):
        trades = [
            {"ret": 0.12, "win": True, "exit": "target", "max_dd": 0.02},
            {"ret": -0.07, "win": False, "exit": "stop", "max_dd": 0.07},
            {"ret": 0.08, "win": True, "exit": "bench", "max_dd": 0.01},
            {"ret": -0.07, "win": False, "exit": "stop", "max_dd": 0.07},
        ]
        s = summarize(trades)
        self.assertEqual(s["n_trades"], 4)
        self.assertEqual(s["win_rate"], 0.5)
        self.assertAlmostEqual(s["profit_factor"], round(0.20 / 0.14, 3), places=3)
        self.assertAlmostEqual(s["max_drawdown"], 0.07, places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
