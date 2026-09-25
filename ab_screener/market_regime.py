"""
市场环境过滤（轻量）
====================
用指数日线判断进攻/中性/防守，避免空头环境滥开仓。
优先本地 SQLite 中的 000300.SH（沪深300）；缺失时用全市场中位涨跌近似。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

# 默认观察指数
DEFAULT_INDEX = "000300.SH"
MARKET_REGIME_POLICY_VERSION = "breakout-market-regime-v1.0.0"
MARKET_REGIME_MINIMUM_HISTORY_ROWS = 25
MARKET_REGIME_MA_WINDOW = 20
MARKET_REGIME_RETURN_LOOKBACK = 20
MARKET_REGIME_MA_TOLERANCE_BPS = 50
MARKET_REGIME_ATTACK_MIN_RETURN = 0.02
MARKET_REGIME_DEFENSE_MAX_RETURN = -0.06
MARKET_REGIME_POLICY = {
    "version": MARKET_REGIME_POLICY_VERSION,
    "benchmark_code": DEFAULT_INDEX,
    "minimum_history_rows": MARKET_REGIME_MINIMUM_HISTORY_ROWS,
    "ma_window": MARKET_REGIME_MA_WINDOW,
    "return_lookback": MARKET_REGIME_RETURN_LOOKBACK,
    "ma_tolerance_bps": MARKET_REGIME_MA_TOLERANCE_BPS,
    "attack_min_return": MARKET_REGIME_ATTACK_MIN_RETURN,
    "defense_max_return": MARKET_REGIME_DEFENSE_MAX_RETURN,
}


def market_regime_policy_identity() -> dict[str, str]:
    """Return the immutable identity shared by research and production."""
    payload = json.dumps(MARKET_REGIME_POLICY, ensure_ascii=False, sort_keys=True)
    return {
        "version": MARKET_REGIME_POLICY_VERSION,
        "config_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
    }


@dataclass
class RegimeResult:
    regime: str  # attack | neutral | defense
    label: str
    allow_new_entries: bool
    max_trade_slots: int
    index_code: str
    as_of: str
    close: float | None
    ma20: float | None
    ret_20d: float | None
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "label": self.label,
            "allow_new_entries": self.allow_new_entries,
            "max_trade_slots": self.max_trade_slots,
            "index_code": self.index_code,
            "as_of": self.as_of,
            "close": self.close,
            "ma20": self.ma20,
            "ret_20d": self.ret_20d,
            "notes": self.notes,
        }


def classify_regime_point(
    close: float,
    ma20: float,
    ret_20d: float,
) -> tuple[str, str, bool, int, list[str]]:
    """防守时 allow=False 且 slots=0，字段语义一致。"""
    notes: list[str] = []
    tolerance = MARKET_REGIME_MA_TOLERANCE_BPS / 10_000
    above = close >= ma20 * (1.0 - tolerance)
    ma_up = ret_20d is not None and ret_20d > 0.0
    if above and ma_up and ret_20d >= MARKET_REGIME_ATTACK_MIN_RETURN:
        notes.append("指数站上MA20且20日涨幅≥2%")
        return "attack", "进攻", True, 15, notes
    # 明确空头：破位或 20 日大跌
    defense_return = MARKET_REGIME_DEFENSE_MAX_RETURN
    if (not above) or (ret_20d is not None and ret_20d <= defense_return):
        if not above and ret_20d is not None and ret_20d <= -0.05:
            notes.append("指数跌破MA20且20日跌幅≥5%")
        elif ret_20d is not None and ret_20d <= -0.06:
            notes.append("20日跌幅≥6%（即使贴近MA也降级防守）")
        else:
            notes.append("指数在MA20下方")
        return "defense", "防守", False, 0, notes
    notes.append("指数中性震荡")
    return "neutral", "中性", True, 10, notes


# Backward-compatible private alias.  New consumers must use the public pure
# function so research and production cannot silently diverge.
_classify = classify_regime_point


def detect_regime_from_index_df(df: pd.DataFrame, index_code: str = DEFAULT_INDEX) -> RegimeResult:
    """df 需含 trade_date, close，升序。"""
    notes: list[str] = []
    if df is None or df.empty or "close" not in df.columns:
        return RegimeResult(
            regime="neutral",
            label="中性(无指数数据)",
            allow_new_entries=True,
            max_trade_slots=10,
            index_code=index_code,
            as_of="",
            close=None,
            ma20=None,
            ret_20d=None,
            notes=["指数K线缺失，默认中性可开仓"],
        )
    d = df.copy()
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d = d.dropna(subset=["close"]).sort_values("trade_date")
    if len(d) < 25:
        return RegimeResult(
            "neutral", "中性(样本不足)", True, 10, index_code,
            str(d["trade_date"].iloc[-1]) if len(d) else "",
            float(d["close"].iloc[-1]) if len(d) else None,
            None, None, ["指数样本不足25日"],
        )
    close = float(d["close"].iloc[-1])
    ma20 = float(d["close"].tail(20).mean())
    c20 = float(d["close"].iloc[-21]) if len(d) >= 21 else float(d["close"].iloc[0])
    ret_20d = (close / c20 - 1.0) if c20 > 0 else 0.0
    regime, label, allow, slots, n2 = classify_regime_point(close, ma20, ret_20d)
    notes.extend(n2)
    return RegimeResult(
        regime=regime,
        label=label,
        allow_new_entries=allow,
        max_trade_slots=slots,
        index_code=index_code,
        as_of=str(d["trade_date"].iloc[-1]),
        close=round(close, 2),
        ma20=round(ma20, 2),
        ret_20d=round(ret_20d, 4),
        notes=notes,
    )


def ensure_index_daily(
    store,
    index_code: str = DEFAULT_INDEX,
    days: int = 120,
    *,
    allow_network: bool = False,
) -> pd.DataFrame:
    """确保指数日线在本地库；显式允许网络时才补齐缺失或过期数据。"""
    from datetime import datetime, timedelta

    idx_df = pd.DataFrame()
    try:
        idx_df = store.load_daily(ts_codes=[index_code])
    except Exception:  # noqa: BLE001
        idx_df = pd.DataFrame()
    expected_as_of = ""
    try:
        expected_as_of = str(store.max_trade_date("daily") or "")
    except Exception:  # noqa: BLE001
        expected_as_of = ""
    index_as_of = ""
    if idx_df is not None and not idx_df.empty and "trade_date" in idx_df.columns:
        index_as_of = str(idx_df["trade_date"].astype(str).max())
    need_pull = (
        idx_df is None
        or idx_df.empty
        or len(idx_df) < 30
        or bool(expected_as_of and index_as_of < expected_as_of)
    )
    if not need_pull:
        return idx_df.sort_values("trade_date")
    if not allow_network:
        return idx_df if idx_df is not None else pd.DataFrame()
    try:
        # 延迟 import，避免无网络时拖垮
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tushare_init import pro
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        # 指数日线：index_daily
        df = pro.index_daily(ts_code=index_code, start_date=start, end_date=end)
        if df is not None and not df.empty:
            # 对齐 daily 表字段
            keep = [c for c in ("ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount") if c in df.columns]
            df = df[keep].copy()
            if "ts_code" not in df.columns:
                df["ts_code"] = index_code
            store.upsert_daily(df)
            return df.sort_values("trade_date")
    except Exception as e:  # noqa: BLE001
        from tushare_init import sanitize_error
        print(f"  [warn] 指数 {index_code} 拉取失败: {sanitize_error(e)[:160]}")
    return idx_df if idx_df is not None else pd.DataFrame()


def detect_regime(
    store=None,
    daily: pd.DataFrame | None = None,
    index_code: str = DEFAULT_INDEX,
    *,
    allow_network: bool = False,
) -> RegimeResult:
    """从 LocalStore 或 daily 大表推断环境。优先真实指数。"""
    idx_df = pd.DataFrame()
    if store is not None:
        idx_df = ensure_index_daily(store, index_code=index_code, allow_network=allow_network)
        expected_as_of = ""
        try:
            expected_as_of = str(store.max_trade_date("daily") or "")
        except Exception:  # noqa: BLE001
            expected_as_of = ""
        index_as_of = ""
        if idx_df is not None and not idx_df.empty and "trade_date" in idx_df.columns:
            index_as_of = str(idx_df["trade_date"].astype(str).max())
        if expected_as_of and index_as_of < expected_as_of:
            return RegimeResult(
                regime="defense",
                label="防守(指数行情过期)",
                allow_new_entries=False,
                max_trade_slots=0,
                index_code=index_code,
                as_of=index_as_of,
                close=None,
                ma20=None,
                ret_20d=None,
                notes=[f"指数行情止于 {index_as_of or '缺失'}，市场日线已到 {expected_as_of}"],
            )
    if idx_df is not None and not idx_df.empty and len(idx_df) >= 25:
        return detect_regime_from_index_df(idx_df, index_code=index_code)
    if daily is not None and not daily.empty:
        d = daily.copy()
        d["close"] = pd.to_numeric(d["close"], errors="coerce")
        # 用每日中位涨跌幅累计，比中位价格更贴近市场方向
        d = d.sort_values(["ts_code", "trade_date"])
        d["pct"] = d.groupby("ts_code")["close"].pct_change()
        g = d.groupby("trade_date")["pct"].median().reset_index()
        g = g.sort_values("trade_date")
        g["close"] = (1 + g["pct"].fillna(0)).cumprod() * 1000.0
        return detect_regime_from_index_df(g[["trade_date", "close"]], index_code="MARKET_MEDIAN_RET")
    return detect_regime_from_index_df(pd.DataFrame(), index_code=index_code)


def _weekdays_only(start: str, end: str) -> list[str]:
    """无交易日历时的兜底：仅排除周末（不含法定节假日）。"""
    from datetime import datetime, timedelta

    d0 = datetime.strptime(start[:8], "%Y%m%d")
    d1 = datetime.strptime(end[:8], "%Y%m%d")
    out: list[str] = []
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:  # Mon-Fri
            out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def resolve_trade_dates(
    store=None,
    *,
    start: str | None = None,
    end: str | None = None,
    trade_dates: list[str] | None = None,
) -> list[str]:
    """Read verified independent calendar dates without network or quote inference.

    The legacy trade_dates argument is intentionally not trusted: callers used
    to pass the dates already present in daily, which cannot detect an outage.
    """
    from zoneinfo import ZoneInfo

    from ab_screener.data.trading_calendar import calendar_window

    end = end or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    result = calendar_window(getattr(store, "db_path", None), today=end, as_of=start, required_days=1)
    return result["open_dates"] if result["verified"] else []


def data_freshness(
    as_of: str,
    today: str | None = None,
    trade_dates: list[str] | None = None,
    store=None,
    now: datetime | None = None,
    *,
    reference_now: datetime | None = None,
    historical: bool = False,
    required_moneyflow_days: int = 5,
) -> dict[str, Any]:
    """Independent-calendar freshness; can_publish_a requires the exact expected day.

    Explicit historical replay uses reference_now or historical=True. Observed
    daily dates cannot certify freshness, and this function never syncs data.
    """
    from ab_screener.data.freshness import assess_data_freshness

    return assess_data_freshness(
        as_of, today=today, trade_dates=trade_dates, store=store, now=now,
        reference_now=reference_now, historical=historical,
        required_moneyflow_days=required_moneyflow_days,
    )
