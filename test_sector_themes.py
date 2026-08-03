"""主题板块映射与配额选取单元测试"""
from __future__ import annotations

import os
import sys

os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from sector_themes import (
    THEME_FILL_ORDER,
    THEME_MIN_COUNT,
    match_themes,
    select_with_sector_quota,
)


def test_match_known_names():
    assert "光模块" in match_themes("通信设备", "中际旭创")
    assert "芯片" in match_themes("半导体", "寒武纪")
    assert "半导体" in match_themes("半导体", "某半导体材料")
    assert "机器人" in match_themes("专用机械", "机器人")
    assert "电力" in match_themes("火力发电", "皖能电力")
    assert "AI应用" in match_themes("软件服务", "科大讯飞")
    print("[PASS] 名称/行业主题映射")


def test_quota_enforced():
    rows = []
    score = 100.0
    # 每个主题造 8 只
    for theme in THEME_FILL_ORDER:
        for i in range(8):
            rows.append({
                "ts_code": f"{theme[:2]}{i:04d}.SZ",
                "名称": f"{theme}测试{i}",
                "行业": {
                    "AI应用": "软件服务",
                    "半导体": "半导体",
                    "光模块": "通信设备",
                    "机器人": "专用机械",
                    "电力": "火力发电",
                    "芯片": "半导体",
                }[theme],
                "综合分": score,
            })
            # 光模块/机器人等靠名称
            if theme == "光模块":
                rows[-1]["名称"] = f"光模块测试{i}"
            if theme == "机器人":
                rows[-1]["名称"] = f"机器人测试{i}"
            if theme == "芯片":
                rows[-1]["名称"] = f"芯片测试{i}"
            score -= 0.1
    # 再加 30 只高分「其他」行业，试图挤占
    for i in range(30):
        rows.append({
            "ts_code": f"OTHER{i:04d}.SZ",
            "名称": f"白酒{i}",
            "行业": "白酒",
            "综合分": 99.0 - i * 0.01,
        })
    df = pd.DataFrame(rows)
    selected, report = select_with_sector_quota(df, top_n=50, score_col="综合分")
    assert len(selected) == 50, len(selected)
    for th, need in THEME_MIN_COUNT.items():
        got = report["theme_counts"].get(th, 0)
        assert got >= need, f"{th} got {got} < {need}; report={report}"
    assert report["quota_ok"] is True
    print("[PASS] 配额强制：每主题≥5，总50")
    print(report["theme_counts"])


def test_quota_shortfall_reported():
    # 仅 AI 有票
    df = pd.DataFrame([
        {"ts_code": f"AI{i}.SZ", "名称": f"软件{i}", "行业": "软件服务", "综合分": 80 - i}
        for i in range(10)
    ])
    selected, report = select_with_sector_quota(df, top_n=50)
    assert report["quota_ok"] is False
    assert report["theme_shortfall"]["光模块"] >= 1
    assert len(selected) == 10
    print("[PASS] 缺口正确报告")


if __name__ == "__main__":
    test_match_known_names()
    test_quota_enforced()
    test_quota_shortfall_reported()
    print("\n全部主题配额测试通过 ✅")
