"""
市场环境过滤（轻量）
====================
用指数日线判断进攻/中性/防守，避免空头环境滥开仓。
优先本地 SQLite 中的 000300.SH（沪深300）；缺失时用全市场中位涨跌近似。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

# 默认观察指数
DEFAULT_INDEX = "000300.SH"


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


def _classify(close: float, ma20: float, ret_20d: float) -> tuple[str, str, bool, int, list[str]]:
    """防守时 allow=False 且 slots=0，字段语义一致。"""
    notes: list[str] = []
    above = close >= ma20 * 0.995
    ma_up = ret_20d is not None and ret_20d > 0.0
    if above and ma_up and ret_20d >= 0.02:
        notes.append("指数站上MA20且20日涨幅≥2%")
        return "attack", "进攻", True, 15, notes
    # 明确空头：破位或 20 日大跌
    if (not above) or (ret_20d is not None and ret_20d <= -0.06):
        if not above and ret_20d is not None and ret_20d <= -0.05:
            notes.append("指数跌破MA20且20日跌幅≥5%")
        elif ret_20d is not None and ret_20d <= -0.06:
            notes.append("20日跌幅≥6%（即使贴近MA也降级防守）")
        else:
            notes.append("指数在MA20下方")
        return "defense", "防守", False, 0, notes
    notes.append("指数中性震荡")
    return "neutral", "中性", True, 10, notes


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
    regime, label, allow, slots, n2 = _classify(close, ma20, ret_20d)
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


def ensure_index_daily(store, index_code: str = DEFAULT_INDEX, days: int = 120) -> pd.DataFrame:
    """确保指数日线在本地库；缺失则尝试 Tushare 拉取。"""
    from datetime import datetime, timedelta

    idx_df = pd.DataFrame()
    try:
        idx_df = store.load_daily(ts_codes=[index_code])
    except Exception:  # noqa: BLE001
        idx_df = pd.DataFrame()
    need_pull = idx_df is None or idx_df.empty or len(idx_df) < 30
    if not need_pull:
        return idx_df.sort_values("trade_date")
    try:
        # 延迟 import，避免无网络时拖垮
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tushare_http import pro
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
        print(f"  [warn] 指数 {index_code} 拉取失败: {str(e)[:80]}")
    return idx_df if idx_df is not None else pd.DataFrame()


def detect_regime(store=None, daily: pd.DataFrame | None = None, index_code: str = DEFAULT_INDEX) -> RegimeResult:
    """从 LocalStore 或 daily 大表推断环境。优先真实指数。"""
    idx_df = pd.DataFrame()
    if store is not None:
        idx_df = ensure_index_daily(store, index_code=index_code)
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
    """解析开市日列表（排除周末+节假日）。

    优先级：显式 trade_dates → 本地 daily 去重日期 → Tushare trade_cal → 仅工作日兜底。
    """
    from datetime import datetime, timedelta

    end = (end or datetime.now().strftime("%Y%m%d"))[:8]
    start = (start or (datetime.strptime(end, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d"))[:8]

    if trade_dates:
        td = sorted({str(x)[:8] for x in trade_dates if str(x).strip()})
        return [d for d in td if start <= d <= end] or td

    # 1) 本地库已有日线交易日（天然不含休市）
    if store is not None:
        try:
            td = store.distinct_dates("daily")
            td = [str(x)[:8] for x in td if start <= str(x)[:8] <= end]
            if len(td) >= 5:
                return td
        except Exception:  # noqa: BLE001
            pass

    # 2) Tushare 交易日历（含法定节假日 is_open=0）
    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tushare_http import pro

        cal = pro.trade_cal(exchange="", start_date=start, end_date=end, fields="cal_date,is_open")
        if cal is not None and not cal.empty:
            opens = sorted(
                cal.loc[cal["is_open"].astype(str).isin(["1", "1.0", "True", "true"]) | (cal["is_open"] == 1), "cal_date"]
                .astype(str)
                .tolist()
            )
            if opens:
                return opens
    except Exception:  # noqa: BLE001
        pass

    # 3) 仅排除周末
    return _weekdays_only(start, end)


def data_freshness(
    as_of: str,
    today: str | None = None,
    trade_dates: list[str] | None = None,
    store=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """数据新鲜度：按**交易日**计算滞后（排除周末与节假日）。

    规则：
    - expected = 截至当前应具备的最新交易日
      · 若今天是交易日且本地时间 < 16:00（收盘数据未齐），expected=上一交易日
      · 否则 expected=≤today 的最近交易日
    - stale_days = expected 与 as_of 之间相差的交易日个数
    - stale_days==0 → 新鲜；==1 → 偏旧；>=2 → 过期
    """
    from datetime import datetime, timedelta

    now = now or datetime.now()
    today = (today or now.strftime("%Y%m%d"))[:8]
    if not as_of:
        return {
            "as_of": "",
            "today": today,
            "stale_days": 999,
            "is_stale": True,
            "label": "无数据",
            "unit": "trading",
            "expected_as_of": "",
            "stale_label": "无数据",
        }

    a = str(as_of)[:8]
    # 日历范围：as_of 往前一点，today 往后一点，保证覆盖
    try:
        start = (datetime.strptime(min(a, today), "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
        end = (datetime.strptime(max(a, today), "%Y%m%d") + timedelta(days=10)).strftime("%Y%m%d")
    except ValueError:
        start, end = a, today

    td = resolve_trade_dates(store, start=start, end=end, trade_dates=trade_dates)
    unit = "trading"
    if not td:
        # 极端兜底：日历日
        try:
            stale = max(0, (datetime.strptime(today, "%Y%m%d") - datetime.strptime(a, "%Y%m%d")).days)
        except ValueError:
            stale = 999
        is_stale = stale > 3
        label = "过期" if is_stale else ("偏旧" if stale > 0 else "新鲜")
        return {
            "as_of": a,
            "today": today,
            "stale_days": stale,
            "is_stale": is_stale,
            "label": label,
            "unit": "calendar",
            "expected_as_of": today,
            "stale_label": f"滞后 {stale} 个日历日",
        }

    # ≤ today 的开市日
    opens = [d for d in td if d <= today]
    if not opens:
        opens = td[:]

    expected = opens[-1]
    # 今日若是交易日且未到 16:00，收盘库尚未更新，期望数据仍为上一交易日
    if expected == today and now.hour < 16 and len(opens) >= 2:
        expected = opens[-2]

    # as_of 对齐到交易日序列
    if a in td:
        a_idx = td.index(a)
    else:
        prior = [d for d in td if d <= a]
        a_idx = td.index(prior[-1]) if prior else 0
        a = td[a_idx]

    if expected in td:
        e_idx = td.index(expected)
    else:
        e_idx = len(td) - 1
        expected = td[e_idx]

    stale = max(0, e_idx - a_idx)
    is_stale = stale >= 2
    if stale == 0:
        label = "新鲜"
    elif stale == 1:
        label = "偏旧"
    else:
        label = "过期"

    return {
        "as_of": a,
        "today": today,
        "stale_days": stale,
        "is_stale": is_stale,
        "label": label,
        "unit": unit,
        "expected_as_of": expected,
        "stale_label": f"滞后 {stale} 个交易日",
    }
