"""扫描用市场数据加载：SQLite/Parquet only，关闭 pickle 读取。"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ab_screener.data.parquet_cache import load_daily_cached
from ab_screener.data.repository import MarketRepository


def load_market_for_scan(
    days: int = 160,
    *,
    force: bool = False,  # 保留签名兼容；force 时跳过 parquet 读
    db_path: str | None = None,
) -> tuple[pd.DataFrame, list[str], pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """返回 (basic, trade_dates, daily, daily_basic, moneyflow, meta)。

    不再读取 out/cache/*.pkl。
    """
    repo = MarketRepository(db_path)
    basic = repo.load_stock_basic()
    all_dates = repo.distinct_dates("daily")
    if not all_dates:
        empty = pd.DataFrame()
        return basic, [], empty, empty, empty, {"source": "empty", "pickle_used": False}

    trade_dates = all_dates[-days:] if len(all_dates) >= days else all_dates
    start, end = trade_dates[0], trade_dates[-1]

    if force:
        daily = repo.load_daily(start=start, end=end)
        meta = {"source": "sqlite", "cache_hit": False, "pickle_used": False}
    else:
        daily, meta = load_daily_cached(repo, start=start, end=end)
        meta["pickle_used"] = False

    dbbasic = repo.load_daily_basic_asof(end)
    mf = pd.DataFrame()  # 扫描阶段按命中再取
    meta["as_of"] = end
    meta["n_dates"] = len(trade_dates)
    meta["n_rows"] = len(daily)
    return basic, trade_dates, daily, dbbasic, mf, meta
