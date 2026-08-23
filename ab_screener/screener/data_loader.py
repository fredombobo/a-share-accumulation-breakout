"""
扫描内核 —— 数据加载（只读/标准化输入）
========================================
职责：把 SQLite/Parquet/Tushare 输入的行情数据加载并标准化为扫描需要的
五元组 (basic, trade_dates, daily, daily_basic, moneyflow)。

不包含：候选生成、信号检测、打分、池划分、进程编排（见 prefilter/evaluator/orchestrator）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 与本包其余模块一致的导入环境：可被 run_screener facade 或 screener/__init__ 先执行
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PYTHONPATH", None)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

import data_fetch
from config import (
    CACHE_DIR as CACHE_DIR_STR,
)
from config import (
    OUT_DIR as OUT_DIR_STR,
)

CACHE_DIR = Path(CACHE_DIR_STR)
OUT_DIR = Path(OUT_DIR_STR)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_market_data(
    days: int,
    force: bool = False,
    db_path: str | None = None,
) -> tuple[pd.DataFrame, list[str], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """加载全市场数据。返回 (stock_basic, trade_dates, daily_df, daily_basic_df, moneyflow_df)

    upgrade system：SQLite 为唯一事实源；Parquet 为可重建缓存；**不再读取 pickle**。
    旧 out/cache/*.pkl 文件保留在磁盘但不参与运行。

    db_path：显式指定只读数据库（扫描内核确定性回归/测试注入用）；None 走默认生产库。
    """
    # 优先 v2 加载器（SQLite + 可选 Parquet）
    try:
        from ab_screener.data.market_loader import load_market_for_scan

        basic, trade_dates, daily, dbbasic, mf, meta = load_market_for_scan(
            days, force=force, db_path=db_path,
        )
        if daily is not None and not getattr(daily, "empty", True):
            print(
                f"[market] source={meta.get('source')} cache_hit={meta.get('cache_hit')} "
                f"rows={meta.get('n_rows')} as_of={meta.get('as_of')} pickle_used=False"
            )
            return basic, trade_dates, daily, dbbasic, mf
        print("[market] v2 加载为空，回退 data_fetch 直连 SQLite/Tushare（仍不读 pickle）")
    except Exception as e:  # noqa: BLE001
        print(f"[market] v2 加载失败，回退: {str(e)[:120]}")

    # 回退：通过 data_fetch（内部优先 LocalStore），禁止 pickle
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    cal = data_fetch.get_trade_cal(start, end)
    trade_dates = cal[-days:] if cal else []

    print("[1/4] 拉取股票列表…")
    basic = data_fetch.get_stock_basic()

    print(f"[2/4] 拉取 {len(trade_dates)} 个交易日全市场日线…")
    daily = data_fetch.get_daily_by_dates(trade_dates, sleep=0.2) if trade_dates else pd.DataFrame()
    print(f"      日线行数: {len(daily)}")

    print("[3/4] 拉取全市场基本面指标…")
    dbbasic = data_fetch.get_daily_basic_by_dates(trade_dates[-1:], sleep=0.2) if trade_dates else pd.DataFrame()
    print(f"      基本面行数: {len(dbbasic)}")

    mf = pd.DataFrame()
    return basic, trade_dates, daily, dbbasic, mf
