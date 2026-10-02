"""横截面机制 G2 检验（docs/prereg/COMMON-CROSS-SECTION-PROTOCOL-V1.md 的实现）。

纯函数、只吃 DataFrame，便于离线测试；数据读取与封存校验在 scripts/run_cross_section_g2.py。

实现要点（与协议逐条对应）：
- 形成日 = 每月最后一个交易日；入场 = 次一交易日开盘；出场 = 下一形成日的次一交易日开盘。
- 收益用 pct_chg 连乘（除权安全）：close_e/open_e × Π(1+pct) × open_x/pre_close_x。
- 入场日缺行或一字涨停 → 不买；出场日缺行或一字跌停 → 顺延到下一个可卖开盘；
  一直不可卖（退市）→ 按最后一个有效收盘计。
- 股票池：沪深、上市满 250 个交易日、形成日未退市、形成日历史名称不含 ST、
  近 20 日 ≥15 天有成交、剔除总市值最小 30%。
- D_t = 最优五分位（可买）等权 − 股票池（可买）等权；净额扣最优五分位换手成本。
- 判定：净 D 块 bootstrap 95% CI 下界 > 0，且毛 D 均值 > 伪信号（组内打乱）毛 D 均值的 95% 分位。
  用毛对毛比较伪信号，避免伪信号高换手成本把门槛压低（比协议文字更严）。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np
import pandas as pd

LISTED_MIN_DAYS = 250
ACTIVE_WINDOW = 20
ACTIVE_MIN_DAYS = 15
MV_CUT = 0.30
QUINTILES = 5
BOOT_REPS = 2000
BOOT_BLOCK = 3
PLACEBO_REPS = 200
SEED = 20260925


@dataclass(frozen=True)
class SignalSpec:
    """信号规格：kind ∈ {max, aturn, ep}；best = 'low' / 'high' 为最优五分位方向。"""

    kind: str
    best: str
    window: int = 20
    long_window: int = 250
    formation_shift: int = 0


PRIMARY_SPECS: dict[str, SignalSpec] = {
    "H-20260925-max-lottery": SignalSpec("max", "low", window=20),
    "H-20260925-abnormal-turnover": SignalSpec("aturn", "low", window=20, long_window=250),
    "H-20260925-ep-value": SignalSpec("ep", "high"),
}
PERTURBATIONS: dict[str, list[SignalSpec]] = {
    "H-20260925-max-lottery": [SignalSpec("max", "low", window=16), SignalSpec("max", "low", window=24)],
    "H-20260925-abnormal-turnover": [
        SignalSpec("aturn", "low", window=16, long_window=250),
        SignalSpec("aturn", "low", window=24, long_window=250),
    ],
    "H-20260925-ep-value": [
        SignalSpec("ep", "high", formation_shift=-1),
        SignalSpec("ep", "high", formation_shift=1),
    ],
}


@dataclass
class Panel:
    """日期 × 股票 的宽表集合（dates 为升序 YYYYMMDD 字符串）。"""

    dates: list[str]
    codes: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    pre_close: np.ndarray
    pct: np.ndarray
    vol: np.ndarray
    turnover: np.ndarray
    pe: np.ndarray
    mv: np.ndarray

    @cached_property
    def growth(self) -> np.ndarray:
        return np.cumsum(np.log1p(np.nan_to_num(self.pct, nan=0.0) / 100.0), axis=0)

    @cached_property
    def one_word_up(self) -> np.ndarray:
        flat = (self.open == self.high) & (self.high == self.low)
        return flat & (self.pct > 0)

    @cached_property
    def one_word_down(self) -> np.ndarray:
        flat = (self.open == self.high) & (self.high == self.low)
        return flat & (self.pct < 0)

    @cached_property
    def sellable(self) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return (
                np.isfinite(self.open) & (self.open > 0) & (self.pre_close > 0) & ~self.one_word_down
            )


def build_panel(daily: pd.DataFrame, basic: pd.DataFrame) -> Panel:
    dates = sorted(daily["trade_date"].astype(str).unique())
    codes = sorted(daily["ts_code"].astype(str).unique())

    def wide(frame: pd.DataFrame, column: str, dtype: type = np.float32) -> np.ndarray:
        # 全市场 2015–2023 约 2,200 日 × 5,000 股：价格类用 float32 控制内存，收益率保留 float64
        if column not in frame.columns:
            return np.full((len(dates), len(codes)), np.nan, dtype=dtype)
        table = frame.pivot(index="trade_date", columns="ts_code", values=column)
        return table.reindex(index=dates, columns=codes).to_numpy(dtype=dtype)

    keys = ["trade_date", "ts_code"]
    daily = daily.assign(trade_date=daily["trade_date"].astype(str), ts_code=daily["ts_code"].astype(str))
    basic = basic.assign(trade_date=basic["trade_date"].astype(str), ts_code=basic["ts_code"].astype(str))
    daily = daily.drop_duplicates(keys, keep="last")
    basic = basic.drop_duplicates(keys, keep="last")
    return Panel(
        dates=dates,
        codes=codes,
        open=wide(daily, "open"),
        high=wide(daily, "high"),
        low=wide(daily, "low"),
        close=wide(daily, "close"),
        pre_close=wide(daily, "pre_close"),
        pct=wide(daily, "pct_chg", np.float64),
        vol=wide(daily, "vol"),
        turnover=wide(basic, "turnover_rate"),
        pe=wide(basic, "pe"),
        mv=wide(basic, "total_mv"),
    )


def formation_indices(dates: list[str], start: str, end: str) -> list[int]:
    """[start, end] 内每月最后一个交易日的下标。"""
    out: list[int] = []
    for i, d in enumerate(dates):
        if not start <= d <= end:
            continue
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        if nxt is None or nxt[:6] != d[:6]:
            out.append(i)
    return out


def signal_at(panel: Panel, f: int, spec: SignalSpec) -> np.ndarray:
    idx = f + spec.formation_shift
    if idx < 0 or idx >= len(panel.dates):
        return np.full(len(panel.codes), np.nan)
    if spec.kind == "max":
        window = panel.pct[max(0, idx - spec.window + 1): idx + 1]
        valid = np.isfinite(window).sum(axis=0)
        with np.errstate(all="ignore"):
            value = np.nanmax(np.where(np.isfinite(window), window, -np.inf), axis=0)
        return np.where(valid >= int(np.ceil(spec.window * 0.75)), value, np.nan)
    if spec.kind == "aturn":
        short = panel.turnover[max(0, idx - spec.window + 1): idx + 1]
        long = panel.turnover[max(0, idx - spec.long_window + 1): idx + 1]
        ok = (np.isfinite(short).sum(axis=0) >= 0.8 * spec.window) & (
            np.isfinite(long).sum(axis=0) >= 0.8 * spec.long_window
        )
        with np.errstate(all="ignore"):
            ratio = np.nanmean(short, axis=0) / np.nanmean(long, axis=0)
        return np.where(ok & np.isfinite(ratio), ratio, np.nan)
    if spec.kind == "ep":
        pe = panel.pe[idx]
        with np.errstate(all="ignore"):
            return np.where(pe > 0, 1.0 / pe, np.nan)
    raise ValueError(f"未知信号 {spec.kind}")


def st_mask(names: pd.DataFrame, codes: list[str], day: str) -> np.ndarray:
    """形成日历史名称含 ST（start_date ≤ day ≤ end_date）。"""
    active = names[(names["start_date"].astype(str) <= day) & (
        names["end_date"].isna() | (names["end_date"].astype(str).replace("", "99991231") >= day)
    )]
    st_codes = set(active.loc[active["name"].astype(str).str.upper().str.contains("ST"), "ts_code"].astype(str))
    return np.array([c in st_codes for c in codes])


def universe_at(panel: Panel, f: int, listing: pd.DataFrame, names: pd.DataFrame) -> np.ndarray:
    day = panel.dates[f]
    codes = panel.codes
    board = np.array([c.endswith((".SH", ".SZ")) for c in codes])
    info = listing.set_index(listing["ts_code"].astype(str))
    list_dates = info["list_date"].astype(str).reindex(codes).fillna("99999999").to_numpy()
    delist = (
        info["delist_date"].astype(str).replace({"None": "", "nan": ""}).reindex(codes).fillna("").to_numpy()
        if "delist_date" in info
        else np.array([""] * len(codes))
    )
    list_idx = np.searchsorted(panel.dates, list_dates)
    seasoned = (f - list_idx) >= LISTED_MIN_DAYS
    alive = np.array([(not d) or d > day for d in delist])
    recent = panel.vol[max(0, f - ACTIVE_WINDOW + 1): f + 1]
    active = (np.nan_to_num(recent) > 0).sum(axis=0) >= ACTIVE_MIN_DAYS
    base = board & seasoned & alive & active & ~st_mask(names, codes, day) & np.isfinite(panel.mv[f])
    if base.sum() == 0:
        return base
    cut = np.nanquantile(panel.mv[f][base], MV_CUT)
    return base & (panel.mv[f] > cut)


def period_returns(panel: Panel, e: int, x: int) -> np.ndarray:
    """入场 e 开盘 → 出场 x 开盘（顺延规则见模块说明）；不可买为 NaN。"""
    growth = panel.growth
    with np.errstate(invalid="ignore"):
        buyable = (
            np.isfinite(panel.open[e]) & (panel.open[e] > 0) & np.isfinite(panel.close[e])
            & ~panel.one_word_up[e]
        )
    r = np.full(len(panel.codes), np.nan)
    first_leg = panel.close[e] / panel.open[e]
    sellable = panel.sellable[x:]
    for j in np.flatnonzero(buyable):
        hits = np.flatnonzero(sellable[:, j])
        if len(hits) == 0:
            valid = np.flatnonzero(np.isfinite(panel.close[e:, j]))
            last = e + int(valid[-1])
            r[j] = first_leg[j] * np.exp(growth[last, j] - growth[e, j]) - 1.0
            continue
        exit_day = x + int(hits[0])
        carry = np.exp(growth[exit_day - 1, j] - growth[e, j])
        r[j] = first_leg[j] * carry * panel.open[exit_day, j] / panel.pre_close[exit_day, j] - 1.0
    return r


def _best_quintile(signal: np.ndarray, members: np.ndarray, best: str) -> np.ndarray:
    ok = members & np.isfinite(signal)
    out = np.zeros_like(members)
    if ok.sum() < QUINTILES:
        return out
    values = signal[ok]
    q = np.quantile(values, [0.2, 0.8])
    chosen = values <= q[0] if best == "low" else values >= q[1]
    out[np.flatnonzero(ok)[chosen]] = True
    return out


def round_trip_cost(day: str, cost: dict[str, Any]) -> float:
    stamp = next(
        (float(s["rate"]) for s in cost["stamp_tax_sell"] if s["from"] <= day <= s["to"]),
        0.001,
    )
    return 2 * float(cost["commission_rate"]) + stamp + 2 * float(cost["slippage_bp_per_side"]) / 10_000


def monthly_series(
    panel: Panel,
    spec: SignalSpec,
    listing: pd.DataFrame,
    names: pd.DataFrame,
    *,
    start: str,
    end: str,
    cost: dict[str, Any],
    placebo_reps: int = PLACEBO_REPS,
    seed: int = SEED,
) -> dict[str, Any]:
    forms = formation_indices(panel.dates, start, end)
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    placebo_by_month: list[np.ndarray] = []
    prev: set[str] = set()
    for k in range(len(forms) - 1):
        f, e = forms[k], forms[k] + 1
        x = forms[k + 1] + 1
        if x >= len(panel.dates) or panel.dates[x] > end:
            break
        members = universe_at(panel, f, listing, names)
        signal = signal_at(panel, f, spec)
        returns = period_returns(panel, e, x)
        buyable = members & np.isfinite(returns)
        best = _best_quintile(signal, members, spec.best) & buyable
        if best.sum() == 0 or buyable.sum() == 0:
            continue
        gross = float(np.mean(returns[best]) - np.mean(returns[buyable]))
        held = {panel.codes[j] for j in np.flatnonzero(best)}
        turnover = 1.0 if not prev else 1.0 - len(held & prev) / len(held)
        prev = held
        net = gross - turnover * round_trip_cost(panel.dates[x], cost)
        rows.append({
            "formation": panel.dates[f], "entry": panel.dates[e], "exit": panel.dates[x],
            "universe": int(buyable.sum()), "best_n": int(best.sum()),
            "gross": gross, "turnover": turnover, "net": net,
        })
        ok_idx = np.flatnonzero(members & np.isfinite(signal))
        sims = np.empty(placebo_reps)
        for s in range(placebo_reps):
            shuffled = signal.copy()
            shuffled[ok_idx] = rng.permutation(signal[ok_idx])
            pb = _best_quintile(shuffled, members, spec.best) & buyable
            sims[s] = np.mean(returns[pb]) - np.mean(returns[buyable]) if pb.any() else np.nan
        placebo_by_month.append(sims)
    placebo = np.nanmean(np.vstack(placebo_by_month), axis=0) if placebo_by_month else np.array([])
    return {"months": rows, "placebo_gross_means": placebo}


def block_bootstrap(values: np.ndarray, *, reps: int = BOOT_REPS, block: int = BOOT_BLOCK, seed: int = SEED) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    blocks = int(np.ceil(n / block))
    means = np.empty(reps)
    for r in range(reps):
        starts = rng.integers(0, n, size=blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        means[r] = values[idx].mean()
    bounds: np.ndarray = np.asarray(np.quantile(means, [0.025, 0.975]), dtype=float)
    return float(bounds[0]), float(bounds[1])


def g2_statistics(series: dict[str, Any]) -> dict[str, Any]:
    months = series["months"]
    net = np.array([m["net"] for m in months], dtype=float)
    gross = np.array([m["gross"] for m in months], dtype=float)
    lo, hi = block_bootstrap(net)
    placebo = series["placebo_gross_means"]
    p95 = float(np.nanquantile(placebo, 0.95)) if len(placebo) else float("nan")
    return {
        "months": len(months),
        "n": int(sum(m["best_n"] for m in months)),
        "mean": float(net.mean()) if len(net) else float("nan"),
        "ci_lo": lo,
        "ci_hi": hi,
        "gross_mean": float(gross.mean()) if len(gross) else float("nan"),
        "placebo_gross_p95": p95,
        "placebo_mean": float(np.nanmean(placebo)) if len(placebo) else float("nan"),
        "mean_turnover": float(np.mean([m["turnover"] for m in months])) if months else float("nan"),
    }
