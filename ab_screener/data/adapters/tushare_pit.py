"""Tushare → PIT 适配器：仅复用根 tushare_init 初始化路径。

契约（implementation P1.1）：
- 禁止裸 requests / 第二套 Token/URL 初始化；真实调用一律
  `from tushare_init import pro`（唯一标准入口）。
- 适配器把 tushare DataFrame 转成 PIT payload 行（业务键字符串化、其余列保留）。
- 测试可注入 fake `pro`（离线），但生产代码路径不创建任何独立连接。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ab_screener.data.migration_intents.aux_history_v2 import ALL_HISTORY_TABLES as HISTORY_TABLES

SOURCE = "tushare"


def get_pro_handle(pro: Any | None = None) -> Any:
    """返回 pro 句柄：显式注入优先（离线测试），否则走根 tushare_init。"""
    if pro is not None:
        return pro
    from tushare_init import pro as root_pro  # 唯一标准初始化路径

    return root_pro


def df_to_pit_rows(df: pd.DataFrame, dataset: str) -> list[dict[str, Any]]:
    """DataFrame → PIT 载荷行：业务键字符串化；缺键列 → 拒绝。"""
    table = f"{dataset}_history"
    if table not in HISTORY_TABLES:
        raise ValueError(f"未知 PIT 数据集: {dataset}")
    key_cols = HISTORY_TABLES[table]
    if df is None or df.empty:
        return []
    missing = [c for c in key_cols if c not in df.columns]
    if missing:
        raise ValueError(f"适配器载荷缺业务键列 {missing}（{dataset}）")
    rows: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        row: dict[str, Any] = {}
        for c, v in record.items():
            if c in key_cols:
                row[c] = str(v) if v is not None and str(v) not in ("", "nan") else None
            else:
                row[c] = v
        if any(row.get(c) in (None, "") for c in key_cols):
            raise ValueError(f"适配器行业务键缺失: {row}（{dataset}）")
        rows.append(row)
    return rows


def fetch_pit_rows(
    dataset: str,
    *,
    start: str,
    end: str,
    pro: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按数据集拉取并转换为 PIT 载荷行（真实网络路径，仅根 tushare_init）。"""
    handle = get_pro_handle(pro)
    kwargs = {"start_date": start, "end_date": end, **(extra or {})}
    if dataset == "daily":
        df = handle.daily(**kwargs)
    elif dataset == "daily_basic":
        df = handle.daily_basic(**kwargs)
    elif dataset == "moneyflow":
        df = handle.moneyflow(**kwargs)
    elif dataset == "adj_factor":
        df = handle.adj_factor(**kwargs)
    elif dataset == "fina_indicator":
        df = handle.fina_indicator(period=end, **(extra or {}))
    elif dataset == "stock_basic":
        df = handle.stock_basic(**(extra or {}))
    elif dataset == "top_list":
        df = handle.top_list(trade_date=end, **(extra or {}))
    elif dataset == "margin":
        df = handle.margin_detail(trade_date=end, **(extra or {}))
    elif dataset == "cyq":
        df = handle.cyq_perf(trade_date=end, **(extra or {}))
    elif dataset == "holder":
        df = handle.top10_holders(ts_code=end, **(extra or {}))  # 按 ts_code 拉取
    else:
        raise ValueError(f"适配器不支持数据集: {dataset}")
    return df_to_pit_rows(df, dataset)
