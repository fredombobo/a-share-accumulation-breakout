"""research_windows 单元测试（不依赖网络 / 不写库）。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from research_windows import recommend_research_plan


def _synth_dates(start: str, n: int) -> list[str]:
    """合成连续日历日作伪交易日（仅测切分逻辑）。"""
    d0 = datetime.strptime(start, "%Y%m%d")
    return [(d0 + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


class TestResearchWindows(unittest.TestCase):
    def test_insufficient_empty(self):
        p = recommend_research_plan([])
        self.assertEqual(p.mode, "insufficient")
        self.assertFalse(p.can_claim_edge)

    def test_insufficient_short(self):
        p = recommend_research_plan(_synth_dates("20250101", 50))
        self.assertEqual(p.mode, "insufficient")

    def test_degraded_split_no_overlap(self):
        dates = _synth_dates("20241210", 400)
        p = recommend_research_plan(dates)
        self.assertEqual(p.mode, "degraded")
        self.assertFalse(p.can_claim_edge)
        self.assertLess(p.is_end, p.oos_start)
        self.assertGreaterEqual(p.is_n_dates, 100)
        self.assertGreaterEqual(p.oos_n_dates, 40)
        self.assertEqual(p.n_dates, 400)
        # OOS 接在 IS 后
        self.assertEqual(p.is_start, dates[0])
        self.assertEqual(p.oos_end, dates[-1])

    def test_full_when_coverage_ok(self):
        # 覆盖 20230801~20260731 且 ≥720 日
        dates = _synth_dates("20230801", 750)
        # 确保最晚覆盖到 OOS 末
        while dates[-1] < "20260731":
            last = datetime.strptime(dates[-1], "%Y%m%d") + timedelta(days=1)
            dates.append(last.strftime("%Y%m%d"))
        p = recommend_research_plan(dates)
        self.assertEqual(p.mode, "full")
        self.assertTrue(p.can_claim_edge)
        self.assertLessEqual(p.is_start, "20230801")
        self.assertGreaterEqual(p.oos_end, "20260731")

    def test_wf_built_for_degraded(self):
        dates = _synth_dates("20241210", 400)
        p = recommend_research_plan(dates)
        self.assertGreaterEqual(len(p.wf_windows), 1)
        for ts, te, vs, ve in p.wf_windows:
            self.assertLess(te, vs)
            self.assertLessEqual(vs, ve)


if __name__ == "__main__":
    unittest.main()
