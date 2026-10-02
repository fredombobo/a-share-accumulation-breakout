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
from pathlib import Path

import pandas as pd

# 与本包其余模块一致的导入环境：可被 run_screener facade 或 screener/__init__ 先执行
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PYTHONPATH", None)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

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
    # A scanner reads one local dataset. Data repair is a separate explicit job.
    # In particular, an injected empty/corrupt test or replay DB must never fall
    # through to the production DB or an online provider.
    from ab_screener.data.market_loader import load_market_for_scan

    basic, trade_dates, daily, dbbasic, mf, meta = load_market_for_scan(
        days, force=force, db_path=db_path,
    )
    print(
        f"[market] source={meta.get('source')} cache_hit={meta.get('cache_hit')} "
        f"rows={meta.get('n_rows')} as_of={meta.get('as_of')} pickle_used=False"
    )
    return basic, trade_dates, daily, dbbasic, mf
