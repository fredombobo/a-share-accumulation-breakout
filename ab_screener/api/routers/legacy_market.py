"""legacy 市场数据路由（G2 拆路由第 3 步）。

迁自 web/backend_app.py 的市场数据域：overview / portfolio / stock / sector-flow /
money-heatmap / stock-flow，及依赖的 K线/信号/财务/板块资金流辅助函数。
共享状态从 ab_screener.api.legacy_state import；候选与信号只读不可变扫描发布记录。
"""
from __future__ import annotations

import json
import math

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from ab_screener.api.deps import get_db_path
from ab_screener.api.legacy_state import (
    _OVERVIEW_CACHE,
    _SECTOR_FLOW_CACHE,
    _SECTOR_FLOW_CACHE_MAX,
    _SIG_CACHE,
    _store,
)
from ab_screener.domain.market_classification import (
    CLASSIFICATIONS,
    ClassificationDefinition,
    get_classification,
)
from scoring import calc_fund_flow_strength
from signals import detect_accumulation_breakout

router = APIRouter(tags=["legacy"])

# Overview is a decision list, not a detail endpoint.  Ten recent bars are
# enough for the card sparkline and keep a 100-candidate response below the
# institutional 300 KiB budget.  Full history remains available from
# /api/stock/{ts_code}.
_OVERVIEW_KLINE_DAYS = 10


@router.get("/api/stock-search")
def stock_search(
    q: str = Query(default="", max_length=80),
    db_path: str = Depends(get_db_path),
) -> list[dict]:
    """Search the full local stock catalog without loading the research universe."""
    query = q.strip()
    if not query:
        return []
    from ab_screener.intelligence.catalog import search_stocks

    return search_stocks(db_path, query)


def _kline_series_for(code: str, limit: int | None = None, start: str | None = None, end: str | None = None) -> list[dict]:
    # SQL 层直接取最近 limit 个交易日，避免全量 K 线拖慢总览。
    # start 由调用方预计算（distinct_dates 全表扫描较贵，不应在循环内重复调用）。
    if limit and limit > 0:
        df = _store.load_daily(ts_codes=[code], start=start, end=end)
    else:
        df = _store.load_daily(ts_codes=[code], end=end)
    if df.empty:
        return []
    df = df.sort_values("trade_date")
    if limit and limit > 0 and len(df) > limit:
        df = df.tail(limit)
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
    """个股信号（带缓存）：每次 overview 对每只重算 detect_accumulation_breakout 很贵，
    以 (code, 最新交易日) 为键缓存；新扫描/新数据后日期变化自动失效。"""
    as_of = _store.max_trade_date("daily") or ""
    key = (code, as_of)
    cached = _SIG_CACHE.get(key)
    if cached is not None:
        return cached
    df = _store.load_daily(ts_codes=[code])
    if df.empty:
        return {}
    df = df.sort_values("trade_date").copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    sig = detect_accumulation_breakout(df)
    _SIG_CACHE[key] = sig
    # 缓存上限：防止内存无限增长（一只约数 KB）
    while len(_SIG_CACHE) > 300:
        _SIG_CACHE.pop(next(iter(_SIG_CACHE)))
    return sig
def _sig_for_many(codes: list[str]) -> dict[str, dict]:
    """批量信号检测（进程池并行，冷请求 30 只从 ~6s 降到 ~1s）。

    未命中缓存的小样本也强制多进程（min_codes_for_pool=1），
    命中缓存的不重算；结果写回 _SIG_CACHE 供后续复用。
    数量很少（<5）时用串行单只计算——spawn 进程池的开销远大于直接算。
    """
    if not codes:
        return {}
    from parallel_scan import detect_many

    as_of = _store.max_trade_date("daily") or ""
    out: dict[str, dict] = {}
    todo: list[str] = []
    for c in codes:
        key = (c, as_of)
        if key in _SIG_CACHE:
            out[c] = _SIG_CACHE[key]
        else:
            todo.append(c)
    if todo:
        if len(todo) < 5:
            # 少量缺失：串行单只计算，避免 spawn 进程池（~3.5s 开销）
            for c in todo:
                try:
                    df = _store.load_daily(ts_codes=[c])
                    if df.empty:
                        sig: dict = {}
                    else:
                        df = df.sort_values("trade_date").copy()
                        df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
                        sig = detect_accumulation_breakout(df)
                except Exception:  # noqa: BLE001
                    sig = {}
                _SIG_CACHE[(c, as_of)] = sig
                out[c] = sig
        else:
            daily = _store.load_daily(ts_codes=todo)
            if not daily.empty:
                sigs = detect_many(todo, daily, workers=None, min_codes_for_pool=1, label="总览信号")
                for c in todo:
                    sig = sigs.get(c) or {}
                    _SIG_CACHE[(c, as_of)] = sig
                    out[c] = sig
        while len(_SIG_CACHE) > 400:
            _SIG_CACHE.pop(next(iter(_SIG_CACHE)))
    return out
def _fina_for(code: str, limit: int = 4, as_of: str | None = None) -> list[dict]:
    df = _store.load_fina_indicator(ts_codes=[code])
    if df.empty:
        return []
    if as_of:
        df = df.loc[df["ann_date"].astype(str) <= as_of]
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
def _classification_or_http(value: str) -> ClassificationDefinition:
    try:
        return get_classification(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNKNOWN_CLASSIFICATION",
                "message": str(exc),
                "details": {"classification": value},
                "retryable": False,
            },
        ) from exc


def _load_sector_flow(
    days: int = 10,
    force: bool = False,
    classification: str = "industry",
) -> tuple[list[str], pd.DataFrame]:
    """按选定维度聚合资金流 pivot（行=日期，列=分组，值=净流入万元）。

    直接从本地 SQLite 读取 moneyflow + stock_basic（无需实时拉取）。
    返回 (dates, pivot_df)。
    """
    definition = _classification_or_http(classification)
    store = _store
    basic = store.load_stock_basic()
    if basic.empty:
        raise HTTPException(status_code=404, detail="本地库无股票数据，请先运行 sync_daily.py")

    mf_dates = store.distinct_dates("moneyflow", limit=days + 5)
    if not mf_dates:
        raise HTTPException(status_code=500, detail="本地库无资金流数据，请先运行 sync_daily.py")
    hit_dates = mf_dates[-days:]
    data_version = store.max_trade_date("moneyflow")
    cache_key = (definition.key, days, data_version)
    if not force and cache_key in _SECTOR_FLOW_CACHE:
        return _SECTOR_FLOW_CACHE[cache_key]

    mf = store.load_moneyflow(start=hit_dates[0], end=hit_dates[-1])
    if mf.empty:
        raise HTTPException(status_code=500, detail="本地库无资金流数据，请先运行 sync_daily.py")

    if definition.column not in basic.columns:
        raise HTTPException(
            status_code=409,
            detail=f"本地 stock_basic 缺少 {definition.column} 分类字段",
        )
    grouping = basic[["ts_code", definition.column]].rename(
        columns={definition.column: "classification_group"}
    )
    grouping["classification_group"] = (
        grouping["classification_group"]
        .fillna("未分类")
        .astype(str)
        .str.strip()
        .replace({"": "未分类", "nan": "未分类"})
    )
    merged = mf.merge(grouping, on="ts_code", how="left")
    merged["classification_group"] = merged["classification_group"].fillna("未分类")
    merged["net"] = pd.to_numeric(merged["net_mf_amount"], errors="coerce").replace(
        [float("inf"), float("-inf")], float("nan"),
    )
    # Aggregate only observed amounts: an all-missing group and an absent
    # group/date remain unknown, while observed zero and offsetting flows stay 0.
    grp = merged.groupby(["trade_date", "classification_group"])["net"].sum(min_count=1).reset_index()
    pivot = grp.pivot(index="trade_date", columns="classification_group", values="net")
    dates = [str(x) for x in pivot.index.tolist()]
    _SECTOR_FLOW_CACHE[cache_key] = (dates, pivot)
    # 缓存上限：只保留最新 N 条，防止按日期无限增长
    while len(_SECTOR_FLOW_CACHE) > _SECTOR_FLOW_CACHE_MAX:
        _SECTOR_FLOW_CACHE.pop(next(iter(_SECTOR_FLOW_CACHE)))
    return dates, pivot
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
    # 旧 scan_result 无池前缀：不默认当可交易 A
    return "B", "unknown"

def _publication_context(run_id: str | None) -> tuple[dict | None, dict, bool]:
    from ab_screener.application.scan_publication import read_scan_publication
    from ab_screener.market_regime import data_freshness

    publication = read_scan_publication(_store.db_path, run_id=run_id)
    if run_id and publication is None:
        raise HTTPException(status_code=404, detail="未找到已成功发布的扫描记录")
    scan_as_of = str(publication.get("as_of") or "") if publication else ""
    try:
        # Recheck against the independent current calendar, never observed daily
        # dates or the historical publication's own clock.
        fresh = data_freshness(scan_as_of, store=_store)
    except Exception:  # noqa: BLE001
        fresh = {"as_of": scan_as_of, "label": "待核对", "is_stale": True,
                 "can_publish_a": False, "blocking_reasons": ["FRESHNESS_UNAVAILABLE"]}
    current = bool(
        publication and not run_id and publication.get("verified") is True
        and publication.get("publication_version") == 2 and publication.get("state") == "READY"
        and fresh.get("can_publish_a") is True and not fresh.get("historical")
    )
    return publication, fresh, current


def _publication_metadata(publication: dict | None) -> dict | None:
    if publication is None:
        return None
    # Do not duplicate candidate payloads or the full strategy audit in a lean API.
    return {key: value for key, value in publication.items()
            if key not in {"candidates", "strategy_snapshot"}}


def _number(value) -> float | None:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def _stored_signal(candidate: dict | None) -> dict:
    """Missing historical fields stay unknown; no current-parameter recalculation."""
    row = candidate or {}
    keys = ("box_high", "box_low", "box_days", "box_amp", "breakout_pct_chg",
            "vol_shrink_ratio", "ma5", "ma10", "ma20")
    return {
        **{key: _number(row.get(key)) for key in keys},
        # scan projection stores box_amp in percent; the signal contract uses
        # a fraction (the detail page formats it as a percentage).
        "box_amp": _number(row.get("box_amp")) / 100 if _number(row.get("box_amp")) is not None else None,
        "breakout_date": row.get("breakout_date") or None,
        "breakout_vol_ratio": _number(row.get("breakout_vol_ratio", row.get("vol_ratio"))),
        "reasons": [str(row["reasons"])] if row.get("reasons") else [],
        "source": "SCAN_PUBLICATION" if candidate is not None else "UNAVAILABLE",
        "as_of": row.get("trade_date") if candidate is not None else None,
    }


@router.get("/api/overview")
def overview(pool: str = "A", run_id: str | None = None):
    """Current published candidates, or an explicitly selected historical run."""
    pool = pool.upper()
    if pool not in {"A", "B", "ALL"}:
        raise HTTPException(status_code=422, detail="pool 必须为 A、B 或 ALL")
    publication, fresh, current = _publication_context(run_id)
    quote_as_of = _store.max_trade_date("daily") or ""
    scan_as_of = str(publication.get("as_of") or "") if publication else ""
    # A same-date empty run, midnight and the 16:00 cutoff must invalidate cache.
    cache_key = (
        str(_store.db_path), pool, run_id, publication.get("run_id") if publication else None,
        publication.get("result_hash") if publication else None, quote_as_of,
        current, publication.get("verified") if publication else None,
        publication.get("qualified_verified") if publication else None,
        publication.get("qualification_integrity_error") if publication else None,
        json.dumps({key: value for key, value in fresh.items() if key != "reference_now"},
                   sort_keys=True, ensure_ascii=False),
    )
    if _OVERVIEW_CACHE["key"] == cache_key and _OVERVIEW_CACHE["payload"] is not None:
        # Re-evaluate the calendar/data gate on every request while keeping
        # the clock alone from forcing expensive chart reconstruction.
        return {**_OVERVIEW_CACHE["payload"], "freshness": fresh}
    rows = publication.get("candidates", []) if publication and (current or run_id) else []
    totals = {"A": 0, "B": 0}
    selected = []
    for row in rows:
        pool_tag, tier = _parse_pool_tier(row.get("reasons"))
        totals[pool_tag] += 1
        if pool == "ALL" or pool == pool_tag:
            selected.append((row, pool_tag, tier))
    codes = [str(row["ts_code"]) for row, _, _ in selected]
    kline_by_code: dict[str, list[dict]] = {}
    if codes:
        chart_end = scan_as_of if run_id else None
        try:
            dates = _store.distinct_dates("daily", limit=None if chart_end else _OVERVIEW_KLINE_DAYS)
            if chart_end:
                dates = [day for day in dates if day <= chart_end][-_OVERVIEW_KLINE_DAYS:]
            start = dates[0] if dates else None
        except Exception:  # noqa: BLE001
            start = None
        try:
            daily = _store.load_daily(ts_codes=codes, start=start, end=chart_end)
            if not daily.empty:
                daily = daily.sort_values(["ts_code", "trade_date"])
                for code, group in daily.groupby("ts_code", sort=False):
                    kline_by_code[str(code)] = [
                        {"trade_date": str(row["trade_date"]),
                         **{key: _number(row.get(key)) for key in ("open", "high", "low", "close", "vol", "amount")}}
                        for _, row in group.tail(_OVERVIEW_KLINE_DAYS).iterrows()
                    ]
        except Exception:  # noqa: BLE001
            # A missing chart is not permission to alter persisted signal evidence.
            kline_by_code = {}
    items = []
    for row, pool_tag, tier in selected:
        code = str(row["ts_code"])
        sig = _stored_signal(row)
        kline = kline_by_code.get(code, [])
        items.append({
            "ts_code": code, "code": code.split(".")[0].zfill(6),
            "name": str(row.get("name") or ""), "industry": str(row.get("industry") or ""),
            **{key: _number(row.get(key)) for key in (
                "price", "mv_yi", "pe", "pb", "turnover", "box_days", "box_amp",
                "vol_ratio", "fund_net_wan", "fund_ratio",
            )},
            "score": _number(row.get("total_score")) or 0,
            "breakout_date": row.get("breakout_date") or "", "reasons": str(row.get("reasons") or ""),
            "pool": pool_tag, "tier": tier, "tradeable": False, "trade": None,
            "candidate_status": "CURRENT_CANDIDATE" if current else "HISTORICAL_CANDIDATE",
            "is_current_candidate": current, "scan_as_of": scan_as_of,
            "signal_as_of": sig["as_of"], "price_as_of": row.get("trade_date") or scan_as_of,
            "quote_as_of": kline[-1]["trade_date"] if kline else None,
            "fund_window": row.get("fund_window"),
            "data_missing_fields": row.get("data_missing_fields") or [],
            "qualified_pool": row.get("qualified_pool"),
            "run_id": publication["run_id"], "kline": kline,
            **{key: sig.get(key) for key in ("box_high", "box_low", "ma5", "ma20")},
        })
    items.sort(key=lambda row: row.get("score") or 0, reverse=True)
    empty_reason = None
    if not publication:
        empty_reason = "暂无成功发布的扫描记录，请先运行扫描"
    elif not current and not run_id:
        empty_reason = "最新扫描仅可作历史参考；发布状态或当前数据未通过核对，请更新数据后重新扫描"
    elif not items:
        empty_reason = f"本次扫描 {pool} 池为零只" if pool != "ALL" else "本次扫描为零只"
    regime = publication.get("regime") if publication else None
    payload = {
        "as_of": scan_as_of, "scan_as_of": scan_as_of or None, "quote_as_of": quote_as_of or None,
        "view_state": "CURRENT" if current else "HISTORICAL" if publication else "NO_PUBLICATION",
        "is_current": current, "publication": _publication_metadata(publication),
        "chart_basis": "THROUGH_SCAN_DATE" if run_id else "LATEST_AVAILABLE",
        "count": len(items), "pool": pool, "freshness": fresh,
        "regime": regime or {"regime": "unknown", "label": "未记录"},
        "pool_totals": totals, "empty_reason": empty_reason, "items": items,
    }
    _OVERVIEW_CACHE["key"] = cache_key
    _OVERVIEW_CACHE["payload"] = payload
    return payload


@router.get("/api/portfolio")
def get_portfolio():
    from portfolio import check_stops, load_portfolio
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


@router.post("/api/portfolio")
def post_portfolio(body: dict):
    raise HTTPException(
        status_code=409,
        detail={"code": "PORTFOLIO_READ_ONLY_MIGRATION",
                "message": "旧持仓接口已只读，请在纸面交易工作台预览并导入",
                "details": {}, "retryable": False},
    )
@router.get("/api/stock/{ts_code}")
def stock_detail(ts_code: str, run_id: str | None = None):
    """行情可自由查询；候选身份和信号仅来自当前或明确选择的发布记录。"""
    code = ts_code.upper()
    publication, fresh, current = _publication_context(run_id)
    candidate = next((row for row in publication.get("candidates", [])
                      if str(row.get("ts_code", "")).upper() == code), None) \
        if publication and (current or run_id) else None
    is_current_candidate = bool(current and candidate is not None)
    pool, tier = _parse_pool_tier(candidate.get("reasons")) if candidate is not None else ("QUERY_ONLY", "unknown")
    candidate_status = ("CURRENT_CANDIDATE" if is_current_candidate else "HISTORICAL_CANDIDATE") \
        if candidate is not None else "QUERY_ONLY"
    basic = _store.load_stock_basic()
    row_meta = basic[basic["ts_code"] == code]
    if row_meta.empty and candidate is None:
        raise HTTPException(status_code=404, detail=f"未找到 {code}")

    historical_end = str(publication["as_of"]) if run_id and publication else None
    kline = _kline_series_for(code, end=historical_end)
    quote_as_of = kline[-1]["trade_date"] if kline else None
    sig = _stored_signal(candidate)
    fina = _fina_for(code, limit=4, as_of=historical_end)

    # Quote/fundamental dates describe this stock, not another stock's table MAX.
    db = _store.load_daily_basic(ts_codes=[code], end=historical_end)
    fund_row = db.sort_values("trade_date").iloc[-1] if not db.empty else None
    latest_date = str(fund_row["trade_date"]) if fund_row is not None else None

    # 资金流（近5日）：只取最近 5 个交易日，修复原来累计全部历史的 bug
    from ab_screener.data.freshness import moneyflow_rows_for_window, moneyflow_window_status

    mf = _store.load_moneyflow(ts_codes=[code], end=historical_end)
    if candidate is not None:
        fund_window = candidate.get("fund_window") or {}
        fund_flow = {
            "as_of": candidate.get("trade_date"), "source": "SCAN_PUBLICATION",
            "observed_dates": fund_window.get("observed_dates", []),
            "net_wan": _number(candidate.get("fund_net_wan")),
            "score": _number(candidate.get("fund_score")),
            "ratio_pct": _number(candidate.get("fund_ratio")), "days": 5,
            "window": fund_window, "complete": fund_window.get("complete") is True,
        }
    else:
        # A freely queried stock may have a missing day even when table MAX is
        # current. Missing observations must not be reported as zero flow.
        fund_window = moneyflow_window_status(
            mf, expected_dates=fresh.get("required_moneyflow_dates"),
            expected_as_of=str(fresh.get("expected_as_of") or ""),
        )
        fund_net, fund_score, fund_ratio = calc_fund_flow_strength(
            moneyflow_rows_for_window(mf, fund_window), days=5,
        ) if fund_window["complete"] else (None, None, None)
        fund_flow = {
            "as_of": fund_window.get("expected_as_of"), "source": "CURRENT_WINDOW",
            "observed_dates": fund_window["observed_dates"],
            "net_wan": round(fund_net, 0) if fund_net is not None else None,
            "score": fund_score, "ratio_pct": round(fund_ratio * 100, 3) if fund_ratio is not None else None,
            "days": 5, "window": fund_window, "complete": fund_window["complete"],
        }

    meta = row_meta.iloc[0] if not row_meta.empty else candidate
    close_px = float(fund_row["close"]) if fund_row is not None and pd.notna(fund_row.get("close")) else None
    return {
        "ts_code": code,
        "name": str(meta.get("name", "")),
        "industry": str(meta.get("industry", "")),
        "area": str(meta.get("area", "")),
        "list_date": str(meta.get("list_date", "")),
        "kline": kline,
        "signal": sig,
        "fundamentals": {
            "as_of": latest_date,
            "pe": float(fund_row["pe"]) if fund_row is not None and pd.notna(fund_row.get("pe")) else None,
            "pb": float(fund_row["pb"]) if fund_row is not None and pd.notna(fund_row.get("pb")) else None,
            "total_mv_wan": float(fund_row["total_mv"]) if fund_row is not None and pd.notna(fund_row.get("total_mv")) else None,
            "circ_mv_wan": float(fund_row["circ_mv"]) if fund_row is not None and pd.notna(fund_row.get("circ_mv")) else None,
            "turnover_rate": float(fund_row["turnover_rate"]) if fund_row is not None and pd.notna(fund_row.get("turnover_rate")) else None,
            "volume_ratio": float(fund_row["volume_ratio"]) if fund_row is not None and pd.notna(fund_row.get("volume_ratio")) else None,
            "close": close_px,
        },
        "fund_flow": fund_flow,
        "data_missing_fields": (candidate or {}).get("data_missing_fields") or [],
        "chart_basis": "THROUGH_SCAN_DATE" if historical_end else "LATEST_AVAILABLE",
        "classification_basis": "CURRENT_STOCK_BASIC",
        "fina": fina,
        "tradeable": False,
        "trade": None,
        "pool": pool,
        "tier": tier,
        "candidate_status": candidate_status,
        "is_current_candidate": is_current_candidate,
        "view_state": "CURRENT" if is_current_candidate else "HISTORICAL" if candidate is not None else "QUERY_ONLY",
        "publication": _publication_metadata(publication),
        "freshness": fresh,
        "run_id": publication["run_id"] if candidate is not None else None,
        "scan_as_of": publication["as_of"] if candidate is not None else None,
        "signal_as_of": sig["as_of"],
        "quote_as_of": quote_as_of,
        "fundamentals_as_of": latest_date,
        "as_of": quote_as_of or latest_date or "",
    }
@router.get("/api/classifications")
def classifications():
    """Return the current, data-backed grouping dimensions."""
    basic = _store.load_stock_basic()
    items: list[dict] = []
    total = max(len(basic), 1)
    for item in CLASSIFICATIONS:
        public = item.public()
        if item.column not in basic.columns:
            items.append({**public, "available": False, "group_count": 0, "coverage_pct": 0.0, "examples": []})
            continue
        values = basic[item.column].dropna().astype(str).str.strip()
        values = values[(values != "") & (values.str.lower() != "nan")]
        counts = values.value_counts()
        items.append(
            {
                **public,
                "available": True,
                "group_count": int(counts.size),
                "coverage_pct": round(float(len(values) / total * 100), 2),
                "examples": [str(value) for value in counts.head(6).index.tolist()],
            }
        )
    return {
        "default": "industry",
        "items": items,
        "limitations": (
            "分类来自当前 stock_basic 快照。申万、中信、概念板块及历史成员尚无本地 PIT 数据，"
            "因此不开放为正式回测分类。"
        ),
    }


@router.get("/api/sector-flow")
def sector_flow(days: int = 10, classification: str = "industry"):
    """按选定分类汇总已取得的每日资金净额和区间排行。"""
    days = max(5, min(days, 20))
    definition = _classification_or_http(classification)
    dates, pivot = _load_sector_flow(days, classification=definition.key)
    industries = {str(c): [round(n, 0) if (n := _number(v)) is not None else None
                          for v in pivot[c].tolist()] for c in pivot.columns}

    cumsum = (pivot.replace([float("inf"), float("-inf")], float("nan"))
              .sum(axis=0, min_count=1).map(_number).dropna().sort_values(ascending=False))
    top_in = [
        {"group": str(k), "industry": str(k), "net_wan": round(float(v), 0)}
        for k, v in cumsum.head(8).items()
    ]
    top_out = [
        {"group": str(k), "industry": str(k), "net_wan": round(float(v), 0)}
        for k, v in cumsum.tail(8).sort_values().items()
    ]

    return {
        "classification": definition.key,
        "classification_title": definition.title,
        "group_label": definition.group_label,
        "dates": dates,
        "days": days,
        "groups": industries,
        "industries": industries,
        "top_in": top_in,
        "top_out": top_out,
        "aggregation_basis": "AVAILABLE_RECORDS_ONLY",
        "note": "按已取得的有效资金净额汇总，不代表完整行业资金流；缺失日期保留为空。",
    }
@router.get("/api/money-heatmap")
def money_heatmap(top: int = 0, classification: str = "industry"):
    """最新交易日资金热力图（treemap 数据）。

    按行业聚合最新交易日 net_mf_amount（万元），返回：
      {trade_date, total_wan, items: [{name, value, net_wan, sector}]}
    value 用绝对值（treemap 面积），net_wan 保留符号（流入红/流出绿）。

    ``top > 0`` 表示每个方向各取 Top N：流入按净额降序，流出按
    净额升序（绝对流出最大优先）。这样单边极端行情不会挤掉另一方向。
    ``top <= 0`` 保留兼容行为，返回全部非零行业并按绝对值排序。
    """
    try:
        definition = _classification_or_http(classification)
        dates, pivot = _load_sector_flow(1, classification=definition.key)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="资金流数据不可用")
    if not dates:
        raise HTTPException(status_code=404, detail="无资金流数据")
    trade_date = dates[-1]
    row = pd.to_numeric(pd.Series(pivot.iloc[-1]), errors="coerce").replace(
        [float("inf"), float("-inf")], float("nan"),
    ).dropna()
    nonzero = row[row != 0]
    if top <= 0:
        selected = nonzero.reindex(nonzero.abs().sort_values(ascending=False).index)
    else:
        per_direction = min(top, 50)
        inflows = nonzero[nonzero > 0].sort_values(ascending=False).head(per_direction)
        outflows = nonzero[nonzero < 0].sort_values(ascending=True).head(per_direction)
        selected = pd.concat([inflows, outflows])
    items = [
        {
            "name": str(k),
            "value": int(abs(round(float(v), 0))),   # treemap 面积用绝对值
            "net_wan": int(round(float(v), 0)),      # 保留正负号
        }
        for k, v in selected.items()
    ]
    total = _number(row.sum(min_count=1))
    total_wan = int(round(total)) if total is not None else None
    return {
        "classification": definition.key,
        "classification_title": definition.title,
        "group_label": definition.group_label,
        "trade_date": trade_date,
        "total_wan": total_wan,
        "items": items,
        "aggregation_basis": "AVAILABLE_RECORDS_ONLY",
        "note": "按已取得的有效资金净额汇总，不代表完整市场资金流；全部缺失时合计为空。",
    }
@router.get("/api/stock/{ts_code}/flow")
def stock_flow(ts_code: str, days: int = 20):
    """个股资金净额趋势，以及所在板块已取得记录的净额汇总。"""
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
    except Exception:  # noqa: BLE001
        s_dates, s_pivot = [], pd.DataFrame()

    # An absent moneyflow row does not prove suspension or zero flow. Use the
    # independent completed-session calendar, and preserve missing values.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ab_screener.data.trading_calendar import calendar_window

    clock = datetime.now(ZoneInfo("Asia/Shanghai"))
    calendar = calendar_window(getattr(store, "db_path", None), today=clock.strftime("%Y%m%d"),
                               before_today=clock.hour < 16, required_days=days)
    if calendar["verified"]:
        s_dates = calendar["required_dates"]
    sector_series = s_pivot[industry] if industry in s_pivot.columns else pd.Series(dtype=float)
    sector_net = [_number(value) for value in sector_series.reindex(s_dates).tolist()]
    by_date = {}
    duplicates = set()
    if not mf_code.empty:
        for _, row in mf_code.sort_values("trade_date").iterrows():
            day = str(row["trade_date"])
            if day in by_date:
                duplicates.add(day)
            by_date[day] = row
    axis_dates = calendar["required_dates"] if calendar["verified"] else sorted(by_date)[-days:]
    flow_rows = []
    for d in axis_dates:
        raw = by_date.get(d) if d not in duplicates else None
        row = raw if raw is not None else {}
        net, belg, blg, selg, slg = (_number(row.get(key)) for key in
                                    ("net_mf_amount", "buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount"))
        buy = belg + blg if belg is not None and blg is not None else None
        sell = selg + slg if selg is not None and slg is not None else None
        flow_rows.append({"trade_date": d, "net_wan": net, "buy_main_wan": buy, "sell_main_wan": sell,
                          "buy_elg_wan": belg, "buy_lg_wan": blg,
                          "status": "DUPLICATE_DATE" if d in duplicates else "MISSING" if raw is None
                          else "INCOMPLETE" if any(value is None for value in (net, buy, sell)) else "OBSERVED"})

    return {
        "ts_code": code,
        "name": str(row_meta.iloc[0].get("name", "")),
        "industry": industry,
        "days": days,
        "calendar_verified": calendar["verified"],
        "calendar_status": calendar["status"],
        "missing_dates": [row["trade_date"] for row in flow_rows if row["status"] != "OBSERVED"],
        "basis": "net_mf_amount",
        "stock_flow": flow_rows,
        "sector_flow": {"dates": s_dates, "net_wan": sector_net,
                        "aggregation_basis": "AVAILABLE_RECORDS_ONLY",
                        "note": "按已取得的有效资金净额汇总，不代表完整行业资金流；缺失日期保留为空。"},
        "as_of": _store.max_trade_date("moneyflow") or "",
    }
