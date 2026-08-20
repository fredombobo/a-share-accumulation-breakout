"""AB 宿主 SQLite 热路径封装（ab_store）。

封装 local_store.LocalStore：
  - ohlcv()：单票日线，含 date(YYYY-MM-DD) 列（喂 signals 前转 date，与
    现网 backend_app._sig_for 口径一致）
  - latest_trade_date()：daily 表最新交易日
  - stock_meta()：stock_basic 单票元信息

SQLite 每操作新连接（LocalStore 内部保证）；本层不做任何写路径。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from logic_platform.data.migrations import run_logic_migrations

_LOGGER = logging.getLogger(__name__)


class ABStore:
    """只读封装宿主 SQLite 热数据。"""

    def __init__(self, db_path: str | Path | None = None, migrate: bool = True):
        from local_store import LocalStore  # 延迟 import：宿主根模块

        self._store = LocalStore(db_path)
        if migrate:
            try:
                run_logic_migrations(self._store.db_path)
            except Exception as exc:  # noqa: BLE001 —— 迁移失败不阻塞只读
                _LOGGER.warning("logic migrations 失败（继续只读）: %s", exc)

    @property
    def db_path(self) -> Path:
        return self._store.db_path

    def ohlcv(
        self,
        ts_code: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """单票日线。返回列：trade_date(YYYYMMDD), date(YYYY-MM-DD), open,
        high, low, close, pre_close, change, pct_chg, vol, amount。
        空数据返回空 DataFrame（带 date 列），不抛异常。
        """
        df = self._store.load_daily(ts_codes=[ts_code], start=start, end=end)
        if df is None or df.empty:
            return pd.DataFrame(
                columns=["ts_code", "trade_date", "date", "open", "high",
                         "low", "close", "pre_close", "change", "pct_chg",
                         "vol", "amount"]
            )
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["date"] = pd.to_datetime(
            df["trade_date"], format="%Y%m%d"
        ).dt.strftime("%Y-%m-%d")
        if limit and len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)
        return df

    def latest_trade_date(self) -> str | None:
        """daily 表最新交易日（YYYYMMDD）。"""
        try:
            return self._store.max_trade_date("daily")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("max_trade_date 读取失败: %s", exc)
            return None

    def stock_meta(self, ts_code: str) -> dict | None:
        """单票元信息（name/industry/market/list_date），未知返回 None。"""
        try:
            basic = self._store.load_stock_basic()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("stock_basic 读取失败: %s", exc)
            return None
        if basic is None or basic.empty:
            return None
        row = basic[basic["ts_code"] == ts_code]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            "ts_code": ts_code,
            "name": r.get("name"),
            "industry": r.get("industry"),
            "market": r.get("market"),
            "list_date": r.get("list_date"),
        }

    def universe_from_stock_basic(self, market: str | None = None) -> list[str]:
        """从 stock_basic 取全市场/指定市场 ts_code 列表。"""
        try:
            basic = self._store.load_stock_basic()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("stock_basic 读取失败: %s", exc)
            return []
        if basic is None or basic.empty:
            return []
        if market:
            basic = basic[basic["market"] == market]
        return basic["ts_code"].dropna().astype(str).tolist()
