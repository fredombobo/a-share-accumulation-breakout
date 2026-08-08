"""方案 B 入场引擎（五步抓主升）单元测试"""
from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PYTHONPATH", None)

from entry_plan_b import detect_plan_b


def mk_bars(closes, vols, pcts):
    n = len(closes)
    return pd.DataFrame({
        "date": [f"2026{m//28+1:02d}{m%28+1:02d}" for m in range(n)],
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "vol": vols, "pct_chg": pcts,
    })


def build_valid():
    """构造满足五步的信号日：阴跌→企稳→放量建仓3日→缩量整理→放量突破"""
    closes, vols, pcts = [], [], []
    # days 0-39: 阴跌 12→10
    for k in range(40):
        closes.append(12.0 - 0.05 * k)
        vols.append(100)
        pcts.append(-0.4)
    # days 40-44: 企稳小阳
    for k in range(5):
        closes.append(10.0 + 0.06 * k)
        vols.append(100)
        pcts.append(0.6)
    # days 45-47: 放量建仓（300/400/500，标杆=400）
    for k, v in enumerate([300, 400, 500]):
        closes.append(10.3 + 0.1 * k)
        vols.append(v)
        pcts.append(3.0 + k)
    # days 48-57: 缩量整理
    for k in range(10):
        closes.append(10.6 + 0.02 * ((k % 3) - 1))
        vols.append(90)
        pcts.append(0.2 if k % 2 else -0.2)
    # day 58: 信号日 放量破标杆 + 涨幅 3%
    closes.append(11.0)
    vols.append(450)
    pcts.append(3.0)
    return mk_bars(closes, vols, pcts)


class TestPlanB(unittest.TestCase):
    def test_full_signal(self):
        r = detect_plan_b(build_valid())
        self.assertTrue(r["is_breakout"], f"应出信号，reasons={r.get('reasons')}")
        self.assertAlmostEqual(r["bench_vol"], 400.0)
        self.assertTrue(r["cond_cross"] and r["cond_ma"] and r["cond_build"] and r["cond_reattack"])

    def test_no_cross_rejected(self):
        """持续阴跌无金叉 → 拒绝"""
        closes = [12.0 - 0.05 * k for k in range(60)]
        vols = [100] * 55 + [450] * 5
        pcts = [-0.4] * 55 + [3.0] * 5
        r = detect_plan_b(mk_bars(closes, vols, pcts))
        self.assertFalse(r["is_breakout"])
        self.assertFalse(r["cond_cross"])

    def test_weak_reattack_rejected(self):
        """信号日量不足标杆 → 拒绝"""
        df = build_valid()
        df.loc[df.index[-1], "vol"] = 200  # < 标杆 400
        r = detect_plan_b(df)
        self.assertFalse(r["is_breakout"])
        self.assertFalse(r["cond_reattack"])

    def test_limit_up_excluded(self):
        """涨停日不可买入 → 拒绝"""
        df = build_valid()
        df.loc[df.index[-1], "pct_chg"] = 10.2
        r = detect_plan_b(df)
        self.assertFalse(r["is_breakout"])

    def test_no_build_seq_rejected(self):
        """金叉有但无建仓序列 → 拒绝"""
        closes = [12.0 - 0.05 * k for k in range(40)] + [10.0 + 0.1 * k for k in range(20)]
        vols = [100] * 59 + [450]
        pcts = [-0.4] * 40 + [0.8] * 19 + [3.0]
        r = detect_plan_b(mk_bars(closes, vols, pcts))
        self.assertFalse(r["is_breakout"])
        self.assertFalse(r["cond_build"])

    def test_short_window(self):
        r = detect_plan_b(mk_bars([10.0] * 20, [100] * 20, [0.5] * 20))
        self.assertFalse(r["is_breakout"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
