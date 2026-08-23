"""V2R-A 确定性回归：扫描内核结果逐字段稳定 + golden 冻结。

frozen market fixture：小型合成市场（3 只严格命中 + 1 只主题观察 + ST/低价/下跌
噪声股），全部数据离线写入临时 SQLite。两次 run_scan（store 注入 + as_of 钉死）
必须逐字段一致；A/B 池代码、评分、理由与顺序必须匹配捕获的 golden
（golden 取自 base commit b6772c3 的原始单块 run_screener.py，同 fixture 全字段一致）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ab_screener.screener.orchestrator import run_scan
from local_store import LocalStore

AS_OF = "20260807"
EXPECTED_A_CODES = ["000001.SZ", "000002.SZ", "000003.SZ"]
EXPECTED_B_CODES = ["000006.SZ"]

# golden：各 A 池标的（代码, 综合分, 信号强度分, 箱体天数, 箱体振幅%, 突破日）
GOLDEN_A = {
    "000001.SZ": {"综合分": 91.6, "信号强度分": 84.7, "箱体天数": 65, "箱体振幅%": 17.4, "突破日": "2026-08-03"},
    "000002.SZ": {"综合分": 92.8, "信号强度分": 86.9, "箱体天数": 45, "箱体振幅%": 12.8, "突破日": "2026-08-03"},
    "000003.SZ": {"综合分": 88.4, "信号强度分": 87.3, "箱体天数": 70, "箱体振幅%": 18.3, "突破日": "2026-08-03"},
}

_N_DATES = 160


def trade_dates(end: str = AS_OF, n: int = _N_DATES) -> list[str]:
    """确定性工作日序列（不含节假日），末尾锚定 end。"""
    from datetime import datetime, timedelta

    end_dt = datetime.strptime(end, "%Y%m%d")
    out: list[str] = []
    d = end_dt
    while len(out) < n:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return sorted(out)


def _trailing(dates: list[str], n: int) -> list[str]:
    return dates[-n:]


def bars_accumulation(
    pre_bars: int = 65,
    box_bars: int = 60,
    box_low: float = 10.0,
    box_high: float = 12.0,
    breakout_pct: float = 0.085,
    vol_base: float = 100.0,
    dates: list[str] | None = None,
    pre_end: float = 11.55,
) -> list[tuple[str, float, float, float, float]]:
    """合成「前段上行 → 横盘箱体 → 放量突破 → 站稳」K 线（确定性，无随机）。"""
    assert dates is not None
    total = pre_bars + box_bars + 5
    dts = _trailing(dates, total)
    rows: list[tuple[str, float, float, float, float]] = []
    for i in range(pre_bars):
        t = i / max(pre_bars - 1, 1)
        close = 8.0 + (pre_end - 8.0) * t
        rows.append((dts[i], close, close * 1.005, close * 0.995, 100.0))
    mid = (box_low + box_high) / 2.0
    for i in range(box_bars):
        k = i
        c = mid + 0.8 * (np.sin(k * 0.55) * 0.55 + np.sin(k * 1.13) * 0.35)
        c = min(max(c, box_low + 0.3), box_high - 0.2)
        if k % 9 == 3:
            lo, hi = box_low + 0.05, c * 1.01 + 0.05
        elif k % 7 == 4:
            hi, lo = box_high - 0.02, c * 0.99 - 0.05
        else:
            lo, hi = c - 0.3, c + 0.3
        hi = min(max(hi, c), box_high + 0.02)
        lo = max(min(lo, c), box_low - 0.02)
        rows.append((dts[pre_bars + i], c, hi, lo, vol_base))
    # 锚定最后箱体收盘贴近箱顶，保证突破日收盘必然突破阻力
    last_box_close = box_high - 0.05
    rows[-1] = (dts[pre_bars + box_bars - 1], last_box_close, box_high + 0.01, last_box_close - 0.1, vol_base)
    prev_close = rows[-1][1]
    bd_close = prev_close * (1 + breakout_pct)
    rows.append((dts[pre_bars + box_bars], bd_close, bd_close * 1.01, prev_close * 0.99, vol_base * 3.0))
    hold = bd_close
    for j in range(4):
        hold = hold * (1.004 if j < 2 else 0.998)
        rows.append((dts[pre_bars + box_bars + 1 + j], hold, hold * 1.005, hold * 0.995, vol_base * 0.9))
    return rows


def bars_low_price(pre: int = 90, price: float = 2.0, dates: list[str] | None = None) -> list[tuple[str, float, float, float, float]]:
    assert dates is not None
    dts = _trailing(dates, pre)
    return [(dts[i], price + 0.02 * np.sin(i * 0.5), price + 0.04, price - 0.02, 60.0) for i in range(pre)]


def bars_downtrend(pre: int = 90, dates: list[str] | None = None) -> list[tuple[str, float, float, float, float]]:
    assert dates is not None
    dts = _trailing(dates, pre)
    rows = []
    for i in range(pre):
        close = 20.0 * (1 - 0.004 * i)
        rows.append((dts[i], close, close * 1.01, close * 0.99, 120.0))
    return rows


def bars_sideways_no_breakout(pre_bars: int = 65, box_bars: int = 60, dates: list[str] | None = None) -> list[tuple[str, float, float, float, float]]:
    assert dates is not None
    dts = _trailing(dates, pre_bars + box_bars)
    rows = []
    for i in range(pre_bars):
        t = i / max(pre_bars - 1, 1)
        close = 8.0 + (11.55 - 8.0) * t
        rows.append((dts[i], close, close * 1.005, close * 0.995, 100.0))
    mid = 11.0
    for i in range(box_bars):
        c = mid + 0.7 * np.sin(i * 0.7)
        rows.append((dts[pre_bars + i], c, c + 0.25, c - 0.25, 100.0))
    return rows


_STOCKS = {
    "000001.SZ": {"name": "测试半导体A", "industry": "半导体", "total_mv": 2_000_000, "pe": 25.0, "pb": 3.0, "turnover": 5.0, "bars": lambda d: bars_accumulation(dates=d)},
    "000002.SZ": {"name": "测试电力B", "industry": "电气设备", "total_mv": 3_000_000, "pe": 18.0, "pb": 2.0, "turnover": 6.0, "bars": lambda d: bars_accumulation(box_bars=45, box_high=16.0, box_low=14.0, breakout_pct=0.045, vol_base=150.0, dates=d)},
    "000003.SZ": {"name": "测试人工智能C", "industry": "软件服务", "total_mv": 1_200_000, "pe": 40.0, "pb": 5.0, "turnover": 3.0, "bars": lambda d: bars_accumulation(box_bars=80, box_high=9.0, box_low=7.5, breakout_pct=0.062, vol_base=80.0, pre_end=8.6, dates=d)},
    "000004.SZ": {"name": "*ST测试D", "industry": "综合", "total_mv": 800_000, "pe": 10.0, "pb": 1.0, "turnover": 1.0, "bars": lambda d: bars_accumulation(dates=d)},
    "000005.SZ": {"name": "测试低价E", "industry": "综合", "total_mv": 400_000, "pe": 10.0, "pb": 1.0, "turnover": 1.0, "bars": lambda d: bars_low_price(dates=d)},
    "000006.SZ": {"name": "测试机器人F", "industry": "通用设备", "total_mv": 600_000, "pe": 30.0, "pb": 4.0, "turnover": 4.0, "bars": lambda d: bars_sideways_no_breakout(dates=d)},
    "000007.SZ": {"name": "测试下跌G", "industry": "银行", "total_mv": 900_000, "pe": 8.0, "pb": 1.0, "turnover": 1.0, "bars": lambda d: bars_downtrend(dates=d)},
    "000300.SH": {"name": "沪深300", "industry": "指数", "total_mv": None, "pe": None, "pb": None, "turnover": None, "bars": lambda d: [(x, 4000.0, 4010.0, 3990.0, 1_000_000.0) for x in d], "index": True},
}


@pytest.fixture(scope="module")
def frozen_market_store(tmp_path_factory) -> LocalStore:
    """冻结市场：离线合成市场写入临时 SQLite，只供读（跑扫描）。"""
    db_path = tmp_path_factory.mktemp("frozen") / "market.db"
    store = LocalStore(db_path)
    dates = trade_dates()
    daily_rows: list[dict] = []
    dbbasic_rows: list[dict] = []
    mf_rows: list[dict] = []
    basic_rows: list[dict] = []
    for code, meta in _STOCKS.items():
        bars = meta["bars"](dates)
        for i, (d, close, high, low, vol) in enumerate(bars):
            prev = bars[i - 1][1] if i else close
            pct = (close / prev - 1) * 100 if prev else 0.0
            daily_rows.append({
                "ts_code": code, "trade_date": d,
                "open": round(close * 0.995, 2), "high": round(high, 2), "low": round(low, 2),
                "close": round(close, 2), "pre_close": round(prev, 2), "change": round(close - prev, 2),
                "pct_chg": round(pct, 2), "vol": vol, "amount": round(vol * close * 10, 2),
            })
        if not meta.get("index"):
            dbbasic_rows.append({
                "ts_code": code, "trade_date": dates[-1], "close": round(bars[-1][1], 2),
                "pe": meta["pe"], "pb": meta["pb"], "ps_ttm": None, "dp": None,
                "total_mv": meta["total_mv"], "circ_mv": meta["total_mv"], "turnover_rate": meta["turnover"],
                "volume_ratio": 1.5,
            })
            basic_rows.append({
                "ts_code": code, "symbol": code.split(".")[0], "name": meta["name"],
                "area": "广东", "industry": meta["industry"], "market": "主板", "list_date": "20180101",
            })
            for d in dates[-5:]:
                mf_rows.append({
                    "ts_code": code, "trade_date": d,
                    "buy_elg_amount": 800.0, "buy_elg_vol": 50.0, "buy_lg_amount": 1200.0, "buy_lg_vol": 80.0,
                    "buy_md_amount": 400.0, "buy_md_vol": 30.0, "buy_sm_amount": 200.0, "buy_sm_vol": 20.0,
                    "net_mf_amount": 500.0, "net_mf_vol": 40.0,
                    "sell_elg_amount": 300.0, "sell_elg_vol": 20.0, "sell_lg_amount": 600.0, "sell_lg_vol": 40.0,
                    "sell_md_amount": 300.0, "sell_md_vol": 25.0, "sell_sm_amount": 200.0, "sell_sm_vol": 18.0,
                    "amount": 100_000.0,
                })
    store.upsert_daily(pd.DataFrame(daily_rows))
    store.upsert_daily_basic(pd.DataFrame(dbbasic_rows))
    store.upsert_moneyflow(pd.DataFrame(mf_rows))
    store.upsert_stock_basic(pd.DataFrame(basic_rows))
    return store


@pytest.fixture(autouse=True)
def _isolate_io(monkeypatch, tmp_path):
    """把图表/导出/xlsx 重定向到临时目录，避免触碰生产 out/ 与真实图表。"""
    import ab_screener.screener.orchestrator as orch

    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orch, "plot_top_kline_batch", lambda *a, **k: {})
    monkeypatch.setattr(orch, "OUT_DIR", out_dir)
    monkeypatch.setattr(orch, "CACHE_DIR", cache_dir)


def normalize(result: dict) -> dict:
    """归一化为 JSON-safe 的可比结构：A/B 池逐字段 + 命中 + 环境 + sig。"""
    def _rows(df) -> list[dict]:
        if df is None or getattr(df, "empty", True):
            return []
        drop = [c for c in df.columns if c in ("_trade_card", "K线图")]
        d = df.drop(columns=drop, errors="ignore").copy()
        d = d.sort_values("ts_code").reset_index(drop=True)
        return json.loads(d.to_json(orient="records", force_ascii=False))

    return {
        "latest_date": result["latest_date"],
        "total_candidates": result["total_candidates"],
        "hits": sorted(result["hits"]),
        "regime": result["regime"].get("regime"),
        "df_a": _rows(result["df_a"]),
        "df_b": _rows(result["df_b"]),
        "sig": {k: v for k, v in sorted(result["sig"].items())},
    }


def test_scanner_golden_result_is_stable(frozen_market_store):
    """相同种子运行两次必须逐字段一致（A/B 池、评分、理由、顺序）。"""
    first = run_scan(store=frozen_market_store, as_of=AS_OF, workers=1, days=160, force=True)
    second = run_scan(store=frozen_market_store, as_of=AS_OF, workers=1, days=160, force=True)
    assert normalize(first) == normalize(second)


def test_scanner_golden_expected_pools(frozen_market_store):
    """A/B 池组成必须匹配 golden（原始单块 run_screener 同 fixture 全字段一致）。"""
    result = run_scan(store=frozen_market_store, as_of=AS_OF, workers=1, days=160, force=True)
    norm = normalize(result)
    assert [row["ts_code"] for row in norm["df_a"]] == EXPECTED_A_CODES
    assert [row["ts_code"] for row in norm["df_b"]] == EXPECTED_B_CODES


def test_scanner_golden_scores_and_reasons(frozen_market_store):
    """各评分、箱体属性与理由必须匹配 golden 值（评分公式/阈值漂移即失败）。"""
    result = run_scan(store=frozen_market_store, as_of=AS_OF, workers=1, days=160, force=True)
    rows_a = {row["ts_code"]: row for row in normalize(result)["df_a"]}
    assert set(rows_a) == set(GOLDEN_A)
    for code, expected in GOLDEN_A.items():
        row = rows_a[code]
        for field, value in expected.items():
            assert row[field] == value, f"{code}.{field} 期望 {value}，实际 {row[field]}"
        assert "放量" in row["入选理由"] and "突破" in row["入选理由"]


def test_scanner_single_and_multi_worker_same_result(frozen_market_store):
    """单 worker / 多 worker 归一化结果一致（Windows spawn 语义回归护栏）。"""
    single = normalize(run_scan(store=frozen_market_store, as_of=AS_OF, workers=1, days=160, force=True))
    multi = normalize(run_scan(store=frozen_market_store, as_of=AS_OF, workers=2, days=160, force=True))
    assert single == multi
