"""
数据获取层
==========
统一通过 tushare_http 初始化（curl_cffi 直连 http://a.sszhixia.cn/）。

数据优先从本地 SQLite 读取（local_store），缺失时增量拉取：
  - stock_basic: 全市场股票列表
  - daily: 全市场日线（按 trade_date 批量，一次=全市场一天）
  - daily_basic: 基本面指标（pe/pb/mv/turnover/volume_ratio）
  - moneyflow: 个股资金流（按 trade_date 批量）
本地回退：/d/stock/Historic data 的 1 分钟 CSV（截至 2025-07-31，聚合为日线）
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 优先使用本目录 vendored 的 tushare_http；兼容旧路径
_HERE = os.path.dirname(os.path.abspath(__file__))
_LEGACY_PICKER = r"E:\openclaw\stock_picker_cn"
for _p in (_HERE, _LEGACY_PICKER):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from tushare_http import pro  # noqa: E402
from local_store import LocalStore, sync_from_tushare  # noqa: E402

# 本地历史数据目录（1分钟级 CSV，聚合为日线用）
LOCAL_HIST_DIR = Path(r"D:\stock\Historic data")
LOCAL_MIN_BAR = 60  # 聚合窗口（分钟）

# 简单内存缓存：避免同一数据重复拉取
_cache: dict[str, pd.DataFrame] = {}

# 本地 SQLite 存储
_store = LocalStore()


def run_sync(days_back: int = 300, moneyflow_days: int | None = None,
             force: bool = False, verbose: bool = True) -> dict:
    """增量同步入口：拉取 Tushare 最新数据写入本地库（每日刷新调用）"""
    return sync_from_tushare(days_back=days_back, moneyflow_days=moneyflow_days,
                             force=force, verbose=verbose)


def _retry(fn, retries: int = 3, delay: float = 2.0):
    """带重试的调用包装（直连服务器偶发 ConnectionReset）"""
    last_err = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(delay * (i + 1))
    raise last_err


def get_trade_cal(start: str, end: str) -> list[str]:
    """获取开市交易日列表（升序）"""
    key = f"cal:{start}:{end}"
    if key in _cache:
        return _cache[key]
    cal = _retry(lambda: pro.trade_cal(exchange="", start_date=start, end_date=end, fields="cal_date,is_open"))
    dates = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())
    _cache[key] = dates
    return dates


def get_stock_basic() -> pd.DataFrame:
    """全市场上市股票列表（含行业/上市日期），优先本地库"""
    if "basic" in _cache:
        return _cache["basic"]
    df = _store.load_stock_basic()
    if df is None or df.empty:
        df = _retry(lambda: pro.stock_basic(
            exchange="", list_status="L",
            fields="ts_code,symbol,name,area,industry,market,list_date",
        ))
        _store.upsert_stock_basic(df)
    _cache["basic"] = df
    return df


def get_daily_by_dates(dates: list[str], sleep: float = 0.0) -> pd.DataFrame:
    """按交易日批量拉全市场日线（优先本地库，缺失日期增量拉取），返回拼接的 DataFrame"""
    if not dates:
        return pd.DataFrame()
    key = f"daily:{dates[0]}:{dates[-1]}"
    if key in _cache:
        return _cache[key]

    stored = _store.load_daily(start=dates[0], end=dates[-1])
    have = set(stored["trade_date"].astype(str).unique()) if not stored.empty else set()
    missing = [d for d in dates if d not in have]

    if missing:
        frames = []
        for dt in missing:
            try:
                df = _retry(lambda d=dt: pro.daily(trade_date=d))
                if not df.empty:
                    frames.append(df)
                    _store.upsert_daily(df)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] daily {dt} 拉取失败: {str(e)[:80]}", file=sys.stderr)
            if sleep:
                time.sleep(sleep)
        if frames:
            fresh = pd.concat(frames, ignore_index=True)
            stored = pd.concat([stored, fresh], ignore_index=True) if not stored.empty else fresh

    out = stored.sort_values(["ts_code", "trade_date"]).reset_index(drop=True) if not stored.empty else pd.DataFrame()
    _cache[key] = out
    return out


def get_daily_basic_by_dates(dates: list[str], sleep: float = 0.0) -> pd.DataFrame:
    """按交易日批量拉全市场基本面指标（优先本地库）"""
    if not dates:
        return pd.DataFrame()
    key = f"dbbasic:{dates[0]}:{dates[-1]}"
    if key in _cache:
        return _cache[key]

    stored = _store.load_daily_basic(start=dates[0], end=dates[-1])
    have = set(stored["trade_date"].astype(str).unique()) if not stored.empty else set()
    missing = [d for d in dates if d not in have]

    if missing:
        frames = []
        for dt in missing:
            try:
                df = _retry(lambda d=dt: pro.daily_basic(
                    trade_date=d,
                    fields="ts_code,trade_date,close,pe,pb,ps_ttm,dp,"
                           "total_mv,circ_mv,turnover_rate,volume_ratio",
                ))
                if not df.empty:
                    frames.append(df)
                    _store.upsert_daily_basic(df)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] daily_basic {dt} 拉取失败: {str(e)[:80]}", file=sys.stderr)
            if sleep:
                time.sleep(sleep)
        if frames:
            fresh = pd.concat(frames, ignore_index=True)
            stored = pd.concat([stored, fresh], ignore_index=True) if not stored.empty else fresh

    out = stored.sort_values(["ts_code", "trade_date"]).reset_index(drop=True) if not stored.empty else pd.DataFrame()
    _cache[key] = out
    return out


def get_moneyflow_by_dates(dates: list[str], sleep: float = 0.0) -> pd.DataFrame:
    """按交易日批量拉全市场个股资金流（优先本地库）"""
    if not dates:
        return pd.DataFrame()
    key = f"mf:{dates[0]}:{dates[-1]}"
    if key in _cache:
        return _cache[key]

    stored = _store.load_moneyflow(start=dates[0], end=dates[-1])
    have = set(stored["trade_date"].astype(str).unique()) if not stored.empty else set()
    missing = [d for d in dates if d not in have]

    if missing:
        frames = []
        for dt in missing:
            try:
                df = _retry(lambda d=dt: pro.moneyflow(trade_date=d))
                if not df.empty:
                    frames.append(df)
                    _store.upsert_moneyflow(df)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] moneyflow {dt} 拉取失败: {str(e)[:80]}", file=sys.stderr)
            if sleep:
                time.sleep(sleep)
        if frames:
            fresh = pd.concat(frames, ignore_index=True)
            stored = pd.concat([stored, fresh], ignore_index=True) if not stored.empty else fresh

    out = stored.sort_values(["ts_code", "trade_date"]).reset_index(drop=True) if not stored.empty else pd.DataFrame()
    _cache[key] = out
    return out


# ── 本地历史数据（1分钟 → 日线聚合，仅作 fallback/长历史） ──

def load_local_daily(ts_code: str) -> pd.DataFrame | None:
    """从 /d/stock/Historic data 读取个股 1 分钟线并聚合为日线。

    文件名格式：SH.600000.csv / SZ.000001.csv，列为 datetime,open,high,low,close,volume,amount
    数据截至 2025-07-31。返回 None 表示无本地数据。
    """
    code = ts_code.split(".")[0]
    exch = "SH" if ts_code.endswith(".SH") else "SZ"
    path = None
    for year in ("2025", "2024", "2023"):
        p = LOCAL_HIST_DIR / year / f"{exch}.{code}.csv"
        if p.exists():
            path = p
            break
    if path is None:
        return None
    try:
        df = pd.read_csv(path, parse_dates=["datetime"])
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 本地CSV读取失败 {path}: {str(e)[:60]}", file=sys.stderr)
        return None
    df = df.set_index("datetime").sort_index()
    # 聚合为日线
    daily = df.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum",
    }).dropna(subset=["close"])
    daily = daily.reset_index()
    daily["date"] = daily["datetime"].dt.strftime("%Y-%m-%d")
    return daily[["date", "open", "high", "low", "close", "volume", "amount"]]


if __name__ == "__main__":
    # 快速自检
    cal = get_trade_cal("20260701", "20260802")
    print("最近开市日:", cal[-3:])
    basic = get_stock_basic()
    print(f"全市场: {len(basic)} 只")
    d = get_daily_by_dates(cal[-1:])
    print(f"{cal[-1]} 日线: {len(d)} 行")
    db = get_daily_basic_by_dates(cal[-1:])
    print(f"{cal[-1]} 基本面: {len(db)} 行")
    mf = get_moneyflow_by_dates(cal[-1:])
    print(f"{cal[-1]} 资金流: {len(mf)} 行")
