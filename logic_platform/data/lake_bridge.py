"""888 data_lake 只读桥（lake_bridge）。

只读 888 数据湖：长历史日线 parquet、交易日历。
路径缺失 / 读取失败一律降级返回 None / 标记 missing，绝不抛异常上抛。

湖 parquet 列（实测）：symbol, trade_date, open, high, low, close,
pre_close, change, pct_chg, volume, turnover —— 无 amount；
symbol 即 ts_code 格式（如 002030.SZ）。

本模块对列做归一化：symbol -> ts_code, volume -> vol, amount -> None
（湖无成交额列，amount_ratio 特征在湖数据下为 None）。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from logic_platform.config import get_config

_LOGGER = logging.getLogger(__name__)

# 归一化后的输出列（与 AB local_store load_daily 对齐的子集）
_NORM_COLS = ["ts_code", "trade_date", "open", "high", "low", "close",
              "pre_close", "change", "pct_chg", "vol", "amount"]


class LakeBridge:
    """888 data_lake 只读访问器。全部方法 try/except 降级，不抛异常。"""

    def __init__(self, lake_root: str | Path | None = None):
        cfg = get_config()
        self.lake_root = Path(lake_root or cfg.lake_root)
        self.daily_dir = self.lake_root / "prices" / "daily"
        self.meta_dir = self.lake_root / "meta"

    # ── 内部工具 ──

    def _norm(self, df: pd.DataFrame) -> pd.DataFrame:
        """湖列 -> 归一化列。缺列补 None，多余列丢弃。"""
        if df is None or df.empty:
            return pd.DataFrame(columns=_NORM_COLS)
        out = df.copy()
        rename = {}
        if "symbol" in out.columns and "ts_code" not in out.columns:
            rename["symbol"] = "ts_code"
        if "volume" in out.columns and "vol" not in out.columns:
            rename["volume"] = "vol"
        out = out.rename(columns=rename)
        for col in _NORM_COLS:
            if col not in out.columns:
                out[col] = None
        return out[_NORM_COLS]

    # ── 公开 API ──

    def read_day(self, trade_date: str) -> pd.DataFrame | None:
        """读单日全市场 parquet（YYYYMMDD）。文件缺失 / 读取失败返回 None。"""
        if not trade_date or not trade_date.isdigit() or len(trade_date) != 8:
            return None
        p = self.daily_dir / f"{trade_date}.parquet"
        if not p.exists():
            return None
        try:
            df = pd.read_parquet(p, engine="pyarrow")
            return self._norm(df)
        except Exception as exc:  # noqa: BLE001 —— 降级不崩
            _LOGGER.warning("lake read_day %s 失败: %s", trade_date, exc)
            return None

    def read_symbol_history(
        self, ts_code: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame | None:
        """读单票区间历史（YYYYMMDD 起止，含两端）。逐日 concat，MVP 够用。"""
        dates = self.available_dates(start, end)
        if not dates:
            return None
        frames: list[pd.DataFrame] = []
        for d in dates:
            day = self.read_day(d)
            if day is not None and not day.empty:
                hit = day[day["ts_code"] == ts_code]
                if not hit.empty:
                    frames.append(hit)
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values("trade_date").reset_index(drop=True)

    def read_trade_calendar(self) -> pd.DataFrame | None:
        """读交易日历 parquet（meta/trade_calendar.parquet）。"""
        p = self.meta_dir / "trade_calendar.parquet"
        if not p.exists():
            return None
        try:
            return pd.read_parquet(p, engine="pyarrow")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("lake 日历读取失败: %s", exc)
            return None

    def available_dates(self, start: str | None = None, end: str | None = None) -> list[str]:
        """daily 目录下可用的 YYYYMMDD 文件列表（升序，可过滤区间）。"""
        if not self.daily_dir.exists():
            return []
        try:
            names = sorted(
                p.name[:-8] for p in self.daily_dir.glob("????????.parquet")
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("lake 目录扫描失败: %s", exc)
            return []
        if start:
            names = [n for n in names if n >= start]
        if end:
            names = [n for n in names if n <= end]
        return names

    def latest_date(self) -> str | None:
        """湖内最新交易日（YYYYMMDD），空返回 None。"""
        dates = self.available_dates()
        return dates[-1] if dates else None

    def status(self) -> dict:
        """湖健康状态：{ok, latest_date, missing}。"""
        missing: list[str] = []
        if not self.lake_root.exists():
            missing.append("lake_root")
        if not self.daily_dir.exists():
            missing.append("prices/daily")
        if not (self.meta_dir / "trade_calendar.parquet").exists():
            missing.append("meta/trade_calendar")
        return {
            "ok": not missing,
            "latest_date": self.latest_date(),
            "missing": missing,
        }
