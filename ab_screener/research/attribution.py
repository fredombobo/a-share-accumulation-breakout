"""假突破归因：A 池形态信号后 5/10/20 日收益分布。

与 ENTRY-DEFINITION-V1 对齐：
- 信号 = detect_accumulation_breakout (strict)
- 入场价 = 突破日下一交易日开盘
- 前向收益相对该入场价（不用突破日收盘，避免偷看）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ab_screener.domain.entry_definition import (
    BREAKOUT_WINDOW_DAYS,
    ENTRY_DEFINITION_ID,
    breakout_in_recent_window,
    definition_snapshot,
    entry_price_from_bars,
    normalize_breakout_date,
    resolve_entry_from_signal,
)
from ab_screener.domain.entry_registry import report_entry_fingerprint
from config import HORIZON_DAYS
from signals import detect_accumulation_breakout

DEFAULT_HORIZONS = (5, 10, 20)

# 分类阈值（研究口径，非交易指令）
TRUE_RET_10 = 0.05       # 10 日 ≥ +5% → 偏真突破
FALSE_RET_10 = -0.05     # 10 日 ≤ -5% → 偏假突破
FALSE_RET_5 = -0.03      # 5 日 ≤ -3% 且 10 日仍负 → 假突破加强


@dataclass
class AttributionEvent:
    ts_code: str
    sample_day: str
    breakout_date: str
    entry_date: str
    entry_price: float
    ret_5: float | None
    ret_10: float | None
    ret_20: float | None
    label: str  # true | false | mixed | incomplete
    box_days: int | None = None
    breakout_vol_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "sample_day": self.sample_day,
            "breakout_date": self.breakout_date,
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 4),
            "ret_5": None if self.ret_5 is None else round(self.ret_5, 6),
            "ret_10": None if self.ret_10 is None else round(self.ret_10, 6),
            "ret_20": None if self.ret_20 is None else round(self.ret_20, 6),
            "label": self.label,
            "box_days": self.box_days,
            "breakout_vol_ratio": self.breakout_vol_ratio,
        }


def _forward_close_return(
    bars: pd.DataFrame,
    entry_index: int,
    entry_price: float,
    horizon: int,
) -> float | None:
    """入场后第 horizon 根 K 线收盘相对入场价收益（entry_index 已是入场日）。"""
    # 研究口径：持有 horizon 个交易日后收盘（不含入场日则 +horizon）
    # 采用：entry 日为 day0，前向 horizon 日收盘 = entry_index + horizon
    j = entry_index + horizon
    if j >= len(bars) or entry_price <= 0:
        return None
    cl = bars.iloc[j].get("close")
    if cl is None or pd.isna(cl):
        return None
    return float(cl) / entry_price - 1.0


def classify_breakout(
    ret_5: float | None,
    ret_10: float | None,
    ret_20: float | None,
) -> str:
    """true / false / mixed / incomplete。"""
    if ret_10 is None and ret_5 is None and ret_20 is None:
        return "incomplete"
    if ret_10 is None:
        return "incomplete"
    if ret_10 >= TRUE_RET_10:
        return "true"
    if ret_10 <= FALSE_RET_10:
        return "false"
    if ret_5 is not None and ret_5 <= FALSE_RET_5 and ret_10 < 0:
        return "false"
    if ret_20 is not None and ret_20 >= TRUE_RET_10 and ret_10 > 0:
        return "true"
    return "mixed"


def event_from_signal(
    *,
    ts_code: str,
    sample_day: str,
    bars: pd.DataFrame,
    signal: dict[str, Any],
    calendar: list[str],
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> AttributionEvent | None:
    if not signal.get("is_breakout"):
        return None
    if not breakout_in_recent_window(
        signal.get("breakout_date"), sample_day, calendar, window=BREAKOUT_WINDOW_DAYS
    ):
        return None
    resolved = resolve_entry_from_signal(bars, signal)
    if not resolved["ok"]:
        return None
    sig_i = int(resolved["signal_index"])
    ent_i = int(resolved["entry_index"])
    entry_px = entry_price_from_bars(bars, sig_i)
    if entry_px is None:
        return None
    entry_date = normalize_breakout_date(bars.iloc[ent_i]["trade_date"])
    rets: dict[int, float | None] = {}
    for h in horizons:
        rets[h] = _forward_close_return(bars, ent_i, entry_px, h)
    r5, r10, r20 = rets.get(5), rets.get(10), rets.get(20)
    return AttributionEvent(
        ts_code=ts_code,
        sample_day=normalize_breakout_date(sample_day),
        breakout_date=resolved["breakout_date"],
        entry_date=entry_date,
        entry_price=entry_px,
        ret_5=r5,
        ret_10=r10,
        ret_20=r20,
        label=classify_breakout(r5, r10, r20),
        box_days=signal.get("box_days"),
        breakout_vol_ratio=signal.get("breakout_vol_ratio"),
    )


def _prepare_bars(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values("trade_date").reset_index(drop=True).copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "vol" not in out.columns and "volume" in out.columns:
        out["vol"] = out["volume"]
    out["vol"] = pd.to_numeric(out.get("vol"), errors="coerce")
    out["trade_date"] = out["trade_date"].astype(str)
    # signals.detect 依赖 date 列
    out["date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
    return out


def collect_attribution_events(
    *,
    store: Any,
    start: str,
    end: str,
    step: int = 5,
    max_codes: int = 400,
    horizon: int = HORIZON_DAYS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    progress_every: int = 50,
) -> list[AttributionEvent]:
    """抽样扫描历史，收集 A 池形态突破事件。"""
    basic = store.load_stock_basic()
    if basic is None or basic.empty:
        return []
    codes = [
        str(c) for c in basic["ts_code"].astype(str).tolist()
        if str(c).endswith((".SH", ".SZ"))
    ][:max_codes]

    cal = [normalize_breakout_date(d) for d in (store.distinct_dates("daily") or [])]
    cal = [d for d in cal if start <= d <= end]
    sample_days = cal[:: max(1, step)]
    if not sample_days:
        return []

    events: list[AttributionEvent] = []
    # 去重：同一股票同一突破日只记一次
    seen: set[tuple[str, str]] = set()
    cal_index = {d: i for i, d in enumerate(cal)}

    for n, code in enumerate(codes, 1):
        # 多取一段未来以便 20 日前向
        df = store.load_daily(ts_codes=[code], start=start, end=end)
        if df is None or len(df) < 60:
            continue
        bars_full = _prepare_bars(df)
        dts = [normalize_breakout_date(x) for x in bars_full["trade_date"].tolist()]
        dts_set = set(dts)

        for day in sample_days:
            day_i = cal_index.get(day, -1)
            if day_i < 60:
                continue
            win_start = cal[max(0, day_i - horizon)]
            # 窗口截止 sample day；前向收益需要 full bars
            win = bars_full[
                (bars_full["trade_date"].map(normalize_breakout_date) >= win_start)
                & (bars_full["trade_date"].map(normalize_breakout_date) <= day)
            ].reset_index(drop=True)
            if len(win) < 60:
                continue
            sig = detect_accumulation_breakout(win)
            if not sig.get("is_breakout"):
                continue
            bd = normalize_breakout_date(sig.get("breakout_date"))
            if not bd or bd not in dts_set:
                continue
            key = (code, bd)
            if key in seen:
                continue
            # 用全历史 bars 解析入场与前向（突破后数据在 sample_day 之后）
            # 将 win 上的信号映射到 full bars
            ev = event_from_signal(
                ts_code=code,
                sample_day=day,
                bars=bars_full,
                signal=sig,
                calendar=cal,
                horizons=horizons,
            )
            if ev is None:
                continue
            seen.add(key)
            events.append(ev)

        if progress_every and n % progress_every == 0:
            print(f"  attribution … {n}/{len(codes)} events={len(events)}")

    return events


def _series_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "win_rate": None}
    arr = np.array(values, dtype=float)
    return {
        "n": int(len(arr)),
        "mean": round(float(arr.mean()), 6),
        "median": round(float(np.median(arr)), 6),
        "p25": round(float(np.percentile(arr, 25)), 6),
        "p75": round(float(np.percentile(arr, 75)), 6),
        "win_rate": round(float((arr > 0).mean()), 4),
    }


def summarize_attribution(events: list[AttributionEvent]) -> dict[str, Any]:
    labels = {"true": 0, "false": 0, "mixed": 0, "incomplete": 0}
    for e in events:
        labels[e.label] = labels.get(e.label, 0) + 1
    n = len(events)
    r5 = [e.ret_5 for e in events if e.ret_5 is not None]
    r10 = [e.ret_10 for e in events if e.ret_10 is not None]
    r20 = [e.ret_20 for e in events if e.ret_20 is not None]
    return {
        "entry_definition_id": ENTRY_DEFINITION_ID,
        "entry_semantic_hash": report_entry_fingerprint(ENTRY_DEFINITION_ID)["entry_semantic_hash"],
        "entry_definition": definition_snapshot(),
        "n_events": n,
        "label_counts": labels,
        "label_rates": {
            k: (round(v / n, 4) if n else None) for k, v in labels.items()
        },
        "ret_5": _series_stats([float(x) for x in r5]),
        "ret_10": _series_stats([float(x) for x in r10]),
        "ret_20": _series_stats([float(x) for x in r20]),
        "thresholds": {
            "true_ret_10": TRUE_RET_10,
            "false_ret_10": FALSE_RET_10,
            "false_ret_5": FALSE_RET_5,
        },
        "disclaimer": "研究辅助，不是投资建议；假/真突破标签为事后统计口径。",
    }


def render_attribution_markdown(summary: dict[str, Any], *, start: str, end: str) -> str:
    lines = [
        "# 假突破归因报告",
        "",
        f"- 区间: `{start}` ~ `{end}`",
        f"- 入场定义: `{summary.get('entry_definition_id')}`",
        f"- 事件数: **{summary.get('n_events')}**",
        "",
        "## 标签分布",
        "",
        "| 标签 | 数量 | 占比 |",
        "|------|------|------|",
    ]
    counts = summary.get("label_counts") or {}
    rates = summary.get("label_rates") or {}
    for lab in ("true", "false", "mixed", "incomplete"):
        lines.append(f"| {lab} | {counts.get(lab, 0)} | {rates.get(lab)} |")
    lines += ["", "## 前向收益（相对次日开盘入场价）", ""]
    for key, title in (("ret_5", "5 日"), ("ret_10", "10 日"), ("ret_20", "20 日")):
        s = summary.get(key) or {}
        lines.append(
            f"- **{title}**: n={s.get('n')} mean={s.get('mean')} "
            f"median={s.get('median')} win_rate={s.get('win_rate')} "
            f"p25={s.get('p25')} p75={s.get('p75')}"
        )
    lines += [
        "",
        "## 阈值",
        "",
        f"```json\n{summary.get('thresholds')}\n```",
        "",
        str(summary.get("disclaimer") or ""),
        "",
    ]
    return "\n".join(lines)
