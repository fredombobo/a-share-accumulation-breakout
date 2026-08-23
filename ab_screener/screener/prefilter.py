"""
扫描内核 —— 预过滤（候选集合 + 理由）
=====================================
职责：剔除 ST/退市/次新/无数据/低价/市值异常的股票，产出候选集合。
只消费读入的 stock_basic + daily_basic，不触碰行情明细与资金流。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pandas as pd

if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PYTHONPATH", None)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

from scoring import (
    is_delisted_name,
    is_st_name,
)


def prefilter(basic: pd.DataFrame, dbbasic: pd.DataFrame) -> pd.DataFrame:
    """剔除 ST/退市/次新/无数据，返回候选 ts_code 列表"""
    if basic is None or basic.empty or "ts_code" not in basic.columns:
        return pd.DataFrame()
    # daily_basic 可能因日历超前/缺数为空：退化为只基于 stock_basic 过滤
    if dbbasic is None or getattr(dbbasic, "empty", True) or "ts_code" not in getattr(dbbasic, "columns", []):
        df = basic.copy()
    else:
        df = basic.merge(dbbasic, on="ts_code", how="inner")
    mask = pd.Series(True, index=df.index)
    mask &= ~df["name"].map(is_st_name)
    mask &= ~df["name"].map(is_delisted_name)

    # 次新股过滤（上市未满1年）
    list_dates = pd.to_datetime(df["list_date"], format="%Y%m%d", errors="coerce")
    mask &= (datetime.now() - list_dates).dt.days >= 250

    # 价格/市值粗筛
    if "close" in df.columns:
        mask &= pd.to_numeric(df["close"], errors="coerce").fillna(0) >= 3.0
    if "total_mv" in df.columns:
        mv_yi = pd.to_numeric(df["total_mv"], errors="coerce").fillna(0) / 10000.0
        mask &= mv_yi.between(20, 4000)

    out = df.loc[mask].copy()
    return out
