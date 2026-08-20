"""资金热力图双向展示回归测试。"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from web.backend_app import money_heatmap


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
        "web.backend_app._load_sector_flow",
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
