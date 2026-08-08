"""
每日增量同步脚本
================
从 Tushare 拉取最新数据写入本地 SQLite（只拉缺失交易日），
供 run_screener / Web 后端复用，避免每次全量拉取浪费 token。

用法：
  python sync_daily.py            # 增量同步（daily + daily_basic + moneyflow + stock_basic）
  python sync_daily.py --force    # 全量重建
  python sync_daily.py --days 60  # 首次建库时回看 60 个交易日
"""
from __future__ import annotations

import argparse
import os
import sys
import time

os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetch import run_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="增量同步 Tushare → 本地 SQLite")
    parser.add_argument("--days", type=int, default=300, help="首次建库回看天数（默认300）")
    parser.add_argument("--moneyflow-days", type=int, default=None, help="资金流回看天数（默认同 --days）")
    parser.add_argument("--force", action="store_true", help="全量重建（忽略库内已有数据）")
    args = parser.parse_args()

    t0 = time.time()
    print(f"=== 开始增量同步（{time.strftime('%Y-%m-%d %H:%M:%S')}）===")
    res = run_sync(
        days_back=args.days,
        moneyflow_days=args.moneyflow_days,
        force=args.force,
        verbose=True,
    )
    print(f"\n=== 同步完成（{time.time()-t0:.1f}s）===")
    print(f"daily 新增交易日: {len(res['daily_dates'])} 行数: {res['rows']['daily']}")
    print(f"daily_basic 行数: {res['rows']['daily_basic']}")
    print(f"moneyflow 新增交易日: {len(res['moneyflow_dates'])} 行数: {res['rows']['moneyflow']}")
    print(f"库内最新 daily: {res['latest_daily']}  moneyflow: {res['latest_moneyflow']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
