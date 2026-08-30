"""资金热力图双向展示回归测试。"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from ab_screener.api.routers.legacy_market import money_heatmap


def test_money_heatmap_returns_every_nonzero_inflow_and_outflow() -> None:
    pivot = pd.DataFrame(
        [
            {
                "流入大": 100.0,
                "流出大": -80.0,
                "流入小": 10.0,
                "流出小": -5.0,
                "零值": 0.0,
            }
        ]
    )
    with patch(
        "ab_screener.api.routers.legacy_market._load_sector_flow",
        return_value=(["20260807"], pivot),
    ):
        result = money_heatmap(top=0)

    assert [item["name"] for item in result["items"]] == [
        "流入大",
        "流出大",
        "流入小",
        "流出小",
    ]
    assert [item["net_wan"] for item in result["items"]] == [100, -80, 10, -5]
    assert result["total_wan"] == 25
    assert result["classification"] == "industry"
    assert result["classification_title"] == "细分行业"


def test_money_heatmap_top_is_applied_per_direction() -> None:
    values = {
        **{f"流入{i:02d}": float(i) for i in range(1, 13)},
        **{f"流出{i:02d}": -float(i) for i in range(1, 14)},
        "零值": 0.0,
    }
    pivot = pd.DataFrame([values])
    with patch(
        "ab_screener.api.routers.legacy_market._load_sector_flow",
        return_value=(["20260828"], pivot),
    ):
        result = money_heatmap(top=10)

    inflows = [item for item in result["items"] if item["net_wan"] > 0]
    outflows = [item for item in result["items"] if item["net_wan"] < 0]

    assert [item["net_wan"] for item in inflows] == list(range(12, 2, -1))
    assert [item["net_wan"] for item in outflows] == list(range(-13, -3, 1))
    assert len(result["items"]) == 20
    assert result["total_wan"] == -13
