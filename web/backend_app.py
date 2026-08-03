"""
横盘吸筹→启动 选股系统 后端 API（SQLite 版 + 异步扫描）
=========================================================
数据统一从本地 SQLite 读取（local_store），不再依赖 xlsx/pkl 扫描产物：
  GET /api/overview          → 最近一次扫描结果（从 scan_result 表读，含 K线+箱体+财报）
  GET /api/stock/{ts_code}   → 个股详情（K线/信号/资金流/基本面/财报）
  POST /api/scan             → 触发异步扫描，立即返回 {task_id}
  GET  /api/scan/status      → 查询最近/指定任务进度（含取消）
  POST /api/scan/{task_id}/cancel → 取消扫描
  GET  /api/sector-flow      → 板块资金流总览
  GET  /api/stock/{ts_code}/flow → 个股+板块资金流趋势
  GET  /api/health

启动：uvicorn backend_app:app --port 8000
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

os.environ.pop("PYTHONPATH", None)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

_BASE = Path(__file__).resolve().parent
_PARENT = _BASE.parent
for _p in (str(_BASE), str(_PARENT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from local_store import LocalStore, sync_fina_for_codes  # noqa: E402
from scoring import (  # noqa: E402
    calc_fund_flow_strength,
    fundamental_filter_passes,
    is_delisted_name,
    is_st_name,
)
from signals import detect_accumulation_breakout  # noqa: E402

app = FastAPI(title="A股 横盘吸筹→启动 选股系统", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 模块级单例（schema 初始化只做一次） ──
_store = LocalStore()
_SECTOR_FLOW_CACHE: dict = {}  # {(days, data_version): (dates, pivot_df)}

# ── 异步扫描任务管理 ──
_SCAN_TASKS: dict[str, dict] = {}
_SCAN_LOCK = threading.Lock()


def _new_task(top: int, days: int) -> str:
    task_id = uuid.uuid4().hex[:12]
    with _SCAN_LOCK:
        _SCAN_TASKS[task_id] = {
            "id": task_id,
            "top": top,
            "days": days,
            "status": "pending",
            "stage": "排队中",
            "progress": 0,
            "started_at": None,
            "finished_at": None,
            "cancel_requested": False,
            "result": None,
            "error": None,
            "log": [],
        }
    return task_id


def _log(task: dict, msg: str) -> None:
    task["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    if len(task["log"]) > 200:
        task["log"] = task["log"][-200:]


def _run_scan_worker(task_id: str, top: int, days: int) -> None:
    """后台线程：A 池可交易 + B 池观察。"""
    with _SCAN_LOCK:
        task = _SCAN_TASKS.get(task_id)
    if task is None:
        return

    def report(stage: str, progress: int, msg: str = "") -> None:
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t is None:
                return
            t["status"] = "running"
            t["stage"] = stage
            t["progress"] = int(progress)
            if msg:
                _log(t, msg)

    try:
        import run_screener

        task["started_at"] = datetime.now().isoformat()
        report("数据准备", 5, f"扫描 A池top={top} days={days}（多核并行）")

        try:
            from local_store import sync_from_tushare
            report("数据准备", 8, "增量同步最新数据…")
            sync_from_tushare(days_back=10, verbose=False)
        except Exception as e:  # noqa: BLE001
            _log(task, f"[warn] 增量同步失败(继续用库内数据): {str(e)[:80]}")

        def progress_cb(stage: str, pct: int, msg: str = "") -> None:
            report(stage, pct, msg)

        # workers=None → config.SCAN_WORKERS（0=自动 cpu-1）
        result = run_screener.run_scan(top=top, days=days, force=False, progress_cb=progress_cb)
        df_a = result.get("df_a")
        df_b = result.get("df_b")
        count_a = 0 if df_a is None or getattr(df_a, "empty", True) else len(df_a)
        count_b = 0 if df_b is None or getattr(df_b, "empty", True) else len(df_b)

        report(
            "完成",
            100,
            f"A={count_a} B={count_b} 环境={result.get('regime', {}).get('label')} "
            f"workers={result.get('workers')} {result.get('elapsed_sec')}s",
        )
        with _SCAN_LOCK:
            task["status"] = "done"
            task["progress"] = 100
            task["finished_at"] = datetime.now().isoformat()
            task["result"] = {
                "status": "ok",
                "latest_date": result.get("latest_date"),
                "total_candidates": result.get("total_candidates", 0),
                "hits": len(result.get("hits") or []),
                "count": count_a,
                "count_a": count_a,
                "count_b": count_b,
                "regime": result.get("regime"),
                "freshness": result.get("freshness"),
                "pool_report": result.get("pool_report"),
                "elapsed_sec": result.get("elapsed_sec"),
            }

    except Exception as e:  # noqa: BLE001
        with _SCAN_LOCK:
            t = _SCAN_TASKS.get(task_id)
            if t:
                t["status"] = "error"
                t["error"] = str(e)
                t["finished_at"] = datetime.now().isoformat()

# ── 数据读取（SQLite） ──

def _kline_series_for(code: str) -> list[dict]:
    df = _store.load_daily(ts_codes=[code])
    if df.empty:
        return []
    df = df.sort_values("trade_date")
    out = []
    for _, r in df.iterrows():
        out.append({
            "trade_date": str(r["trade_date"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "vol": float(r["vol"]),
            "amount": float(r["amount"]) if pd.notna(r.get("amount")) else None,
        })
    return out


def _sig_for(code: str) -> dict:
    df = _store.load_daily(ts_codes=[code])
    if df.empty:
        return {}
    df = df.sort_values("trade_date").copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return detect_accumulation_breakout(df)


def _fina_for(code: str, limit: int = 4) -> list[dict]:
    df = _store.load_fina_indicator(ts_codes=[code])
    if df.empty:
        return []
    df = df.sort_values("ann_date", ascending=False).head(limit)
    out = []
    for _, r in df.iterrows():
        out.append({
            "ann_date": str(r["ann_date"]),
            "end_date": str(r["end_date"]),
            "roe": float(r["roe"]) if pd.notna(r.get("roe")) else None,
            "roe_waa": float(r["roe_waa"]) if pd.notna(r.get("roe_waa")) else None,
            "roa": float(r["roa"]) if pd.notna(r.get("roa")) else None,
            "grossprofit_margin": float(r["grossprofit_margin"]) if pd.notna(r.get("grossprofit_margin")) else None,
            "netprofit_margin": float(r["netprofit_margin"]) if pd.notna(r.get("netprofit_margin")) else None,
            "or_yoy": float(r["or_yoy"]) if pd.notna(r.get("or_yoy")) else None,
            "netprofit_yoy": float(r["netprofit_yoy"]) if pd.notna(r.get("netprofit_yoy")) else None,
            "debt_to_assets": float(r["debt_to_assets"]) if pd.notna(r.get("debt_to_assets")) else None,
            "current_ratio": float(r["current_ratio"]) if pd.notna(r.get("current_ratio")) else None,
            "quick_ratio": float(r["quick_ratio"]) if pd.notna(r.get("quick_ratio")) else None,
            "ocf_to_or": float(r["ocf_to_or"]) if pd.notna(r.get("ocf_to_or")) else None,
            "eps": float(r["eps"]) if pd.notna(r.get("eps")) else None,
            "bps": float(r["bps"]) if pd.notna(r.get("bps")) else None,
        })
    return out


def _load_sector_flow(days: int = 10, force: bool = False) -> tuple[list[str], pd.DataFrame]:
    """按行业聚合的全市场资金流 pivot（行=日期，列=行业，值=净流入万元）。

    直接从本地 SQLite 读取 moneyflow + stock_basic（无需实时拉取）。
    返回 (dates, pivot_df)。
    """
    store = _store
    basic = store.load_stock_basic()
    if basic.empty:
        raise HTTPException(status_code=404, detail="本地库无股票数据，请先运行 sync_daily.py")

    mf_dates = store.distinct_dates("moneyflow", limit=days + 5)
    if not mf_dates:
        raise HTTPException(status_code=500, detail="本地库无资金流数据，请先运行 sync_daily.py")
    hit_dates = mf_dates[-days:]
    data_version = store.max_trade_date("moneyflow")
    cache_key = (days, data_version)
    if not force and cache_key in _SECTOR_FLOW_CACHE:
        return _SECTOR_FLOW_CACHE[cache_key]

    mf = store.load_moneyflow(start=hit_dates[0], end=hit_dates[-1])
    if mf.empty:
        raise HTTPException(status_code=500, detail="本地库无资金流数据，请先运行 sync_daily.py")

    merged = mf.merge(basic[["ts_code", "industry"]], on="ts_code", how="left")
    merged["net"] = pd.to_numeric(merged["net_mf_amount"], errors="coerce").fillna(0)
    grp = merged.groupby(["trade_date", "industry"])["net"].sum().reset_index()
    pivot = grp.pivot(index="trade_date", columns="industry", values="net").fillna(0)
    dates = [str(x) for x in pivot.index.tolist()]
    _SECTOR_FLOW_CACHE[cache_key] = (dates, pivot)
    return dates, pivot


# ── API ──

@app.get("/api/scan/status")
def scan_status(task_id: str | None = None):
    """查询扫描进度。默认返回最新任务；指定 task_id 返回该任务。"""
    with _SCAN_LOCK:
        if task_id:
            task = _SCAN_TASKS.get(task_id)
            if task is None:
                raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
            return {k: task[k] for k in ("id", "status", "stage", "progress", "cancel_requested", "result", "error")}
        if not _SCAN_TASKS:
            return {"status": "idle", "stage": "无任务", "progress": 0}
        # 返回最新任务
        latest = max(_SCAN_TASKS.values(), key=lambda t: t.get("started_at") or "")
        return {k: latest[k] for k in ("id", "status", "stage", "progress", "cancel_requested", "result", "error")}


@app.post("/api/scan/{task_id}/cancel")
def cancel_scan(task_id: str):
    with _SCAN_LOCK:
        task = _SCAN_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
        if task["status"] in ("done", "error", "cancelled"):
            return {"status": task["status"], "stage": task["stage"]}
        task["cancel_requested"] = True
        return {"status": "cancelling", "stage": task["stage"]}


class ScanRequest(BaseModel):
    top: int = 15  # A 池默认可交易数量
    days: int = 160
    force: bool = False


def _parse_pool_tier(reasons: str) -> tuple[str, str]:
    """从 reasons 前缀解析 池 与 层级。无前缀旧数据标 unknown，避免误入 A。"""
    import re
    s = str(reasons or "")
    m = re.search(r"\[池([AB])\|([^\|\]]+)", s)
    if m:
        return m.group(1), m.group(2).strip()
    if "theme_fill" in s or "主题强制" in s:
        return "B", "theme_fill"
    if "relaxed" in s or "放宽" in s:
        return "B", "relaxed"
    if "[池" in s:
        return "A", "strict"
    # 旧 scan_result 无池前缀：不默认当可交易 A
    return "B", "unknown"


@app.post("/api/scan")
def start_scan(req: ScanRequest):
    """触发异步扫描，立即返回 task_id"""
    top = max(5, min(req.top, 50))
    days = max(30, min(req.days, 250))
    task_id = _new_task(top, days)
    t = threading.Thread(target=_run_scan_worker, args=(task_id, top, days), daemon=True)
    t.start()
    return {"status": "started", "task_id": task_id, "top": top, "days": days}


@app.get("/api/health")
def health():
    from market_regime import data_freshness, detect_regime
    as_of = _store.max_trade_date("daily") or ""
    # 按交易日历（排除周末/节假日）计算滞后
    fresh = data_freshness(as_of, store=_store)
    try:
        regime = detect_regime(store=_store)
        reg = regime.to_dict()
    except Exception:  # noqa: BLE001
        reg = {"regime": "unknown", "label": "未知"}
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "as_of": as_of,
        "freshness": fresh,
        "regime": reg,
    }


@app.get("/api/overview")
def overview(pool: str = "A"):
    """最新扫描结果。pool=A|B|ALL"""
    from market_regime import data_freshness, detect_regime
    from trade_plan import build_trade_card

    df = _store.load_scan_result()
    if df.empty:
        raise HTTPException(status_code=404, detail="暂无扫描结果，请先运行扫描")

    latest = str(df["trade_date"].iloc[0]) if "trade_date" in df.columns else ""
    # 交易日滞后（排除周末/节假日）
    fresh = data_freshness(latest, store=_store)
    try:
        regime = detect_regime(store=_store).to_dict()
    except Exception:  # noqa: BLE001
        regime = {"regime": "neutral", "label": "中性"}

    items = []
    for _, row in df.iterrows():
        code = row["ts_code"]
        reasons = str(row.get("reasons") or "")
        pool_tag, tier = _parse_pool_tier(reasons)
        if pool.upper() == "A" and pool_tag != "A":
            continue
        if pool.upper() == "B" and pool_tag != "B":
            continue
        sig = _sig_for(code)
        price = None if pd.isna(row["price"]) else float(row["price"])
        card = build_trade_card(
            price=price,
            box_high=sig.get("box_high"),
            box_low=sig.get("box_low"),
            breakout_date=str(row.get("breakout_date") or ""),
            tier=tier,
            regime=regime.get("regime", "neutral"),
            score=float(row["total_score"]) if pd.notna(row["total_score"]) else None,
        )
        item = {
            "ts_code": code,
            "code": str(code).split(".")[0].zfill(6),
            "name": str(row["name"]),
            "price": price,
            "industry": str(row["industry"]),
            "mv_yi": None if pd.isna(row["mv_yi"]) else float(row["mv_yi"]),
            "pe": None if pd.isna(row["pe"]) else float(row["pe"]),
            "pb": None if pd.isna(row["pb"]) else float(row["pb"]),
            "turnover": None if pd.isna(row["turnover"]) else float(row["turnover"]),
            "score": float(row["total_score"]) if pd.notna(row["total_score"]) else 0,
            "box_days": int(row["box_days"]) if pd.notna(row["box_days"]) else None,
            "box_amp": float(row["box_amp"]) if pd.notna(row["box_amp"]) else None,
            "vol_ratio": float(row["vol_ratio"]) if pd.notna(row["vol_ratio"]) else None,
            "fund_net_wan": float(row["fund_net_wan"]) if pd.notna(row["fund_net_wan"]) else None,
            "fund_ratio": float(row["fund_ratio"]) if pd.notna(row["fund_ratio"]) else None,
            "breakout_date": str(row["breakout_date"]),
            "reasons": reasons,
            "pool": pool_tag,
            "tier": tier,
            "tradeable": card["tradeable"],
            "trade": card,
            "fina": _fina_for(code, limit=1),
            "kline": _kline_series_for(code),
            "box_high": sig.get("box_high"),
            "box_low": sig.get("box_low"),
            "ma5": sig.get("ma5"),
            "ma20": sig.get("ma20"),
        }
        items.append(item)

    # A 池按分数排序
    items.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {
        "as_of": latest,
        "count": len(items),
        "pool": pool.upper(),
        "freshness": fresh,
        "regime": regime,
        "items": items,
    }


@app.get("/api/portfolio")
def get_portfolio():
    from portfolio import load_portfolio, check_stops
    data = load_portfolio()
    # 最新价
    prices = {}
    for pos in data.get("positions") or []:
        code = str(pos.get("ts_code", "")).upper()
        d = _store.load_daily(ts_codes=[code])
        if d is not None and not d.empty:
            d = d.sort_values("trade_date")
            prices[code] = float(pd.to_numeric(d.iloc[-1]["close"], errors="coerce") or 0)
    alerts = check_stops(prices)
    return {"portfolio": data, "alerts": alerts, "prices": prices}


@app.post("/api/portfolio")
def post_portfolio(body: dict):
    from portfolio import upsert_position, remove_position, load_portfolio
    action = body.get("action", "upsert")
    code = body.get("ts_code") or body.get("code")
    if not code:
        raise HTTPException(400, "ts_code required")
    if action == "remove":
        return remove_position(code)
    return upsert_position(
        code,
        name=body.get("name") or "",
        cost=body.get("cost"),
        shares=body.get("shares"),
        stop_loss=body.get("stop_loss"),
        note=body.get("note") or "",
    )


@app.get("/api/stock/{ts_code}")
def stock_detail(ts_code: str):
    """个股详情：K线/信号/资金流/基本面/财报"""
    code = ts_code.upper()
    basic = _store.load_stock_basic()
    row_meta = basic[basic["ts_code"] == code]
    if row_meta.empty:
        raise HTTPException(status_code=404, detail=f"未找到 {code}")

    kline = _kline_series_for(code)
    sig = _sig_for(code)
    fina = _fina_for(code, limit=4)

    # 基本面（最新交易日）
    latest_date = _store.max_trade_date("daily_basic") or ""
    db = _store.load_daily_basic(ts_codes=[code])
    fund_row = db[db["trade_date"] == latest_date] if latest_date and not db.empty else db
    fund_row = fund_row.iloc[0] if not fund_row.empty else None

    # 资金流（近5日）
    mf = _store.load_moneyflow(ts_codes=[code])
    mf_rows = mf if not mf.empty else pd.DataFrame()
    fund_net, fund_score, fund_ratio = calc_fund_flow_strength(mf_rows)

    meta = row_meta.iloc[0]
    from trade_plan import build_trade_card
    from market_regime import detect_regime
    try:
        reg = detect_regime(store=_store).regime
    except Exception:  # noqa: BLE001
        reg = "neutral"
    close_px = float(fund_row["close"]) if fund_row is not None and pd.notna(fund_row.get("close")) else None
    # 从 scan_result 推断层级
    scan = _store.load_scan_result()
    tier = "strict"
    if not scan.empty:
        hit = scan[scan["ts_code"] == code]
        if not hit.empty:
            _, tier = _parse_pool_tier(str(hit.iloc[0].get("reasons") or ""))
    trade = build_trade_card(
        price=close_px,
        box_high=sig.get("box_high"),
        box_low=sig.get("box_low"),
        breakout_date=sig.get("breakout_date"),
        tier=tier,
        regime=reg,
    )
    return {
        "ts_code": code,
        "name": str(meta.get("name", "")),
        "industry": str(meta.get("industry", "")),
        "area": str(meta.get("area", "")),
        "list_date": str(meta.get("list_date", "")),
        "kline": kline,
        "signal": {
            "box_high": sig.get("box_high"),
            "box_low": sig.get("box_low"),
            "box_days": sig.get("box_days"),
            "box_amp": sig.get("box_amp"),
            "breakout_date": sig.get("breakout_date"),
            "breakout_vol_ratio": sig.get("breakout_vol_ratio"),
            "breakout_pct_chg": sig.get("breakout_pct_chg"),
            "vol_shrink_ratio": sig.get("vol_shrink_ratio"),
            "ma5": sig.get("ma5"),
            "ma10": sig.get("ma10"),
            "ma20": sig.get("ma20"),
            "reasons": sig.get("reasons", []),
        },
        "fundamentals": {
            "pe": float(fund_row["pe"]) if fund_row is not None and pd.notna(fund_row.get("pe")) else None,
            "pb": float(fund_row["pb"]) if fund_row is not None and pd.notna(fund_row.get("pb")) else None,
            "total_mv_wan": float(fund_row["total_mv"]) if fund_row is not None and pd.notna(fund_row.get("total_mv")) else None,
            "circ_mv_wan": float(fund_row["circ_mv"]) if fund_row is not None and pd.notna(fund_row.get("circ_mv")) else None,
            "turnover_rate": float(fund_row["turnover_rate"]) if fund_row is not None and pd.notna(fund_row.get("turnover_rate")) else None,
            "volume_ratio": float(fund_row["volume_ratio"]) if fund_row is not None and pd.notna(fund_row.get("volume_ratio")) else None,
            "close": close_px,
        },
        "fund_flow": {
            "net_wan": round(fund_net, 0),
            "score": fund_score,
            "ratio_pct": round(fund_ratio * 100, 3),
            "days": len(mf_rows),
        },
        "fina": fina,
        "trade": trade,
        "tier": tier,
        "as_of": latest_date or _store.max_trade_date("daily") or "",
    }


@app.get("/api/sector-flow")
def sector_flow(days: int = 10):
    """板块资金流总览：各行业近 N 日每日主力净流入 + Top 流入/流出排行"""
    days = max(5, min(days, 20))
    dates, pivot = _load_sector_flow(days)
    industries = {str(c): [round(float(v), 0) for v in pivot[c].tolist()] for c in pivot.columns}

    cumsum = pivot.sum(axis=0).sort_values(ascending=False)
    top_in = [{"industry": str(k), "net_wan": round(float(v), 0)} for k, v in cumsum.head(8).items()]
    top_out = [{"industry": str(k), "net_wan": round(float(v), 0)} for k, v in cumsum.tail(8).sort_values().items()]

    return {
        "dates": dates,
        "days": days,
        "industries": industries,
        "top_in": top_in,
        "top_out": top_out,
    }


@app.get("/api/stock/{ts_code}/flow")
def stock_flow(ts_code: str, days: int = 20):
    """个股资金流趋势 + 所在板块资金流趋势（近 N 日，可观察建仓/出逃时段）"""
    code = ts_code.upper()
    days = max(5, min(days, 20))
    basic = _store.load_stock_basic()
    row_meta = basic[basic["ts_code"] == code]
    if row_meta.empty:
        raise HTTPException(status_code=404, detail=f"未找到 {code}")
    industry = str(row_meta.iloc[0].get("industry", ""))

    # 个股资金流（直接从本地库读）
    store = _store
    mf_code = pd.DataFrame()
    try:
        mf_all = store.load_moneyflow(ts_codes=[code])
        mf_code = mf_all if not mf_all.empty else pd.DataFrame()
    except Exception:  # noqa: BLE001
        pass

    # 板块资金流（复用/触发聚合缓存）
    try:
        s_dates, s_pivot = _load_sector_flow(min(days, 20))
        sector_net = [round(float(s_pivot.loc[d, industry]), 0) if industry in s_pivot.columns else 0.0
                      for d in s_dates if d in s_pivot.index]
    except Exception:  # noqa: BLE001
        s_dates, sector_net = [], []

    # 个股资金流：按交易日补齐停牌日（net=0），保证与板块轴长度一致
    flow_rows = []
    if not mf_code.empty:
        mf_code = mf_code.sort_values("trade_date")
        by_date = {str(r["trade_date"]): r for _, r in mf_code.iterrows()}
        axis_dates = s_dates if s_dates else [str(x) for x in mf_code["trade_date"]][-days:]
        for d in axis_dates:
            r = by_date.get(d)
            if r is not None:
                net = float(r.get("net_mf_amount") or 0)
                buy_main = float(r.get("buy_elg_amount") or 0) + float(r.get("buy_lg_amount") or 0)
                sell_main = float(r.get("sell_elg_amount") or 0) + float(r.get("sell_lg_amount") or 0)
                flow_rows.append({
                    "trade_date": str(r["trade_date"]),
                    "net_wan": round(net, 0),
                    "buy_main_wan": round(buy_main, 0),
                    "sell_main_wan": round(sell_main, 0),
                    "buy_elg_wan": round(float(r.get("buy_elg_amount") or 0), 0),
                    "buy_lg_wan": round(float(r.get("buy_lg_amount") or 0), 0),
                })
            else:
                flow_rows.append({
                    "trade_date": d,
                    "net_wan": 0,
                    "buy_main_wan": 0,
                    "sell_main_wan": 0,
                    "buy_elg_wan": 0,
                    "buy_lg_wan": 0,
                })

    return {
        "ts_code": code,
        "name": str(row_meta.iloc[0].get("name", "")),
        "industry": industry,
        "days": days,
        "stock_flow": flow_rows,
        "sector_flow": {"dates": s_dates, "net_wan": sector_net},
        "as_of": _store.max_trade_date("moneyflow") or "",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
