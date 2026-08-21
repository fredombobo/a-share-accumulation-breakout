"""legacy 市场数据路由（G2 拆路由第 3 步）。

迁自 web/backend_app.py 的市场数据域：overview / portfolio / stock / sector-flow /
money-heatmap / stock-flow，及依赖的 K线/信号/财务/板块资金流辅助函数。
共享状态从 ab_screener.api.legacy_state import；build_trade_card 来自 trade_plan、
data_freshness/detect_regime 来自 market_regime（函数内延迟 import）。
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException

from ab_screener.api.legacy_state import (
    _OVERVIEW_CACHE,
    _SECTOR_FLOW_CACHE,
    _SECTOR_FLOW_CACHE_MAX,
    _SIG_CACHE,
    _store,
)
from scoring import calc_fund_flow_strength
from signals import detect_accumulation_breakout

router = APIRouter(tags=["legacy"])

def _kline_series_for(code: str, limit: int | None = None, start: str | None = None) -> list[dict]:
    # SQL 层直接取最近 limit 个交易日，避免全量 K 线拖慢总览。
    # start 由调用方预计算（distinct_dates 全表扫描较贵，不应在循环内重复调用）。
    if limit and limit > 0:
        df = _store.load_daily(ts_codes=[code], start=start) if start else _store.load_daily(ts_codes=[code])
    else:
        df = _store.load_daily(ts_codes=[code])
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
    if "[池" in s:
        return "A", "strict"
    # 旧 scan_result 无池前缀：不默认当可交易 A
    return "B", "unknown"
@router.get("/api/overview")
def overview(pool: str = "A"):
    """最新扫描结果。pool=A|B|ALL。

    无结果时返回 200 + 空列表（不再 404），便于前端保留缓存/提示扫一次。
    """
    from market_regime import data_freshness, detect_regime
    from trade_plan import build_trade_card

    pool = pool.upper()
    as_of_key = _store.max_trade_date("daily") or ""
    # 轻量列表缓存：数据日期 + 池 不变则直接返回（本机热请求 <1s）
    cache_key = (as_of_key, pool)
    if _OVERVIEW_CACHE["key"] == cache_key and _OVERVIEW_CACHE["payload"] is not None:
        return _OVERVIEW_CACHE["payload"]

    df = _store.load_scan_result()
    if df is None or getattr(df, "empty", True):
        as_of = _store.max_trade_date("daily") or ""
        try:
            fresh = data_freshness(as_of, store=_store)
        except Exception:  # noqa: BLE001
            fresh = {"label": "未知", "is_stale": True}
        try:
            regime = detect_regime(store=_store).to_dict()
        except Exception:  # noqa: BLE001
            regime = {"regime": "neutral", "label": "中性"}
        payload: dict[str, object] = {
            "as_of": as_of,
            "count": 0,
            "pool": pool.upper(),
            "items": [],
            "freshness": fresh,
            "regime": regime,
            "empty_reason": "暂无扫描结果，请先运行扫描",
        }
        _OVERVIEW_CACHE["key"] = cache_key
        _OVERVIEW_CACHE["payload"] = payload
        return payload

    latest = str(df["trade_date"].iloc[0]) if "trade_date" in df.columns else ""
    # 交易日滞后（排除周末/节假日）
    fresh = data_freshness(latest, store=_store)
    try:
        regime = detect_regime(store=_store).to_dict()
    except Exception:  # noqa: BLE001
        regime = {"regime": "neutral", "label": "中性"}

    items = []
    n_a = n_b = 0
    # 第一遍：筛选池 + 统计，收集候选代码
    pool_codes: list[str] = []
    for _, row in df.iterrows():
        code = row["ts_code"]
        reasons = str(row.get("reasons") or "")
        pool_tag, tier = _parse_pool_tier(reasons)
        if pool_tag == "A":
            n_a += 1
        elif pool_tag == "B":
            n_b += 1
        if pool.upper() == "A" and pool_tag != "A":
            continue
        if pool.upper() == "B" and pool_tag != "B":
            continue
        pool_codes.append(code)

    # 信号字段：优先读 scan_result 持久化的 box_high/box_low/ma5/ma20（零计算），
    # 缺失（老数据）才批量并行重算
    sig_map: dict[str, dict] = {}
    need_recalc: list[str] = []
    for _, row in df.iterrows():
        code = row["ts_code"]
        reasons = str(row.get("reasons") or "")
        pool_tag, _tier = _parse_pool_tier(reasons)
        if pool.upper() == "A" and pool_tag != "A":
            continue
        if pool.upper() == "B" and pool_tag != "B":
            continue
        bh = row.get("box_high")
        calculated = row.get("sig_calculated")
        if (bh is not None and pd.notna(bh)) or calculated == 1:
            # 已计算过：直接用持久化字段（box_high 为 NULL 但 sig_calculated=1 是无箱体，合法）
            sig_map[code] = {
                "box_high": float(bh) if bh is not None and pd.notna(bh) else None,
                "box_low": float(row["box_low"]) if pd.notna(row.get("box_low")) else None,
                "ma5": float(row["ma5"]) if pd.notna(row.get("ma5")) else None,
                "ma20": float(row["ma20"]) if pd.notna(row.get("ma20")) else None,
            }
        else:
            need_recalc.append(code)
    if need_recalc:
        sig_map.update(_sig_for_many(need_recalc))

    # 日期窗口只算一次（distinct_dates 全表扫描较贵，避免在循环内重复）
    kline_start = None
    try:
        kline_start = _store.distinct_dates("daily", limit=60)[0]
    except Exception:  # noqa: BLE001
        kline_start = None

    # 批量加载 K 线一次（30 只 × 60 天），按 code 分组复用，避免循环内 30 次串行查库
    kline_by_code: dict[str, list[dict]] = {}
    if pool_codes:  # 空列表时跳过，避免 IN () 退化为全表扫描
        try:
            _kd = _store.load_daily(ts_codes=pool_codes, start=kline_start)
            if not _kd.empty:
                _kd = _kd.sort_values(["ts_code", "trade_date"])
                for _c, _g in _kd.groupby("ts_code", sort=False):
                    _rows = []
                    for _, _r in _g.iterrows():
                        _rows.append({
                            "trade_date": str(_r["trade_date"]),
                            "open": float(_r["open"]),
                            "high": float(_r["high"]),
                            "low": float(_r["low"]),
                            "close": float(_r["close"]),
                            "vol": float(_r["vol"]),
                            "amount": float(_r["amount"]) if pd.notna(_r.get("amount")) else None,
                        })
                    kline_by_code[str(_c)] = _rows
        except Exception:  # noqa: BLE001
            kline_by_code = {}

    # 第二遍：组装轻量条目（不再逐只串行重算信号/加载全量 K 线）
    for _, row in df.iterrows():
        code = row["ts_code"]
        reasons = str(row.get("reasons") or "")
        pool_tag, _tier = _parse_pool_tier(reasons)
        if pool.upper() == "A" and pool_tag != "A":
            continue
        if pool.upper() == "B" and pool_tag != "B":
            continue
        tier = _tier
        sig = sig_map.get(code) or {}
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
            # 总览为轻量列表：不返回 fina（财务详情走 /api/stock/{ts_code}），
            # kline 只返回最近 60 条供迷你图，避免 30 个候选全量 K 线 + 财务拖慢响应
            "kline": kline_by_code.get(code) or _kline_series_for(code, limit=60, start=kline_start),
            "box_high": sig.get("box_high"),
            "box_low": sig.get("box_low"),
            "ma5": sig.get("ma5"),
            "ma20": sig.get("ma20"),
        }
        items.append(item)

    # A 池按分数排序
    items.sort(key=lambda x: x.get("score") or 0, reverse=True)
    empty_reason = None
    if not items and (n_a + n_b) > 0:
        if pool.upper() == "A" and n_b > 0:
            empty_reason = f"当前 A 池为空（库内 B 池 {n_b} 只，可切换到 B 或全部）"
        elif pool.upper() == "B" and n_a > 0:
            empty_reason = f"当前 B 池为空（库内 A 池 {n_a} 只，可切换到 A 或全部）"
    payload = {
        "as_of": latest,
        "count": len(items),
        "pool": pool.upper(),
        "freshness": fresh,
        "regime": regime,
        "pool_totals": {"A": n_a, "B": n_b},
        "empty_reason": empty_reason,
        "items": items,
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

    # 资金流（近5日）：只取最近 5 个交易日，修复原来累计全部历史的 bug
    mf = _store.load_moneyflow(ts_codes=[code])
    mf_rows = mf if not mf.empty else pd.DataFrame()
    fund_net, fund_score, fund_ratio = calc_fund_flow_strength(mf_rows, days=5)

    meta = row_meta.iloc[0]
    from market_regime import detect_regime
    from trade_plan import build_trade_card
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
            "days": 5,
        },
        "fina": fina,
        "trade": trade,
        "tier": tier,
        "as_of": latest_date or _store.max_trade_date("daily") or "",
    }
@router.get("/api/sector-flow")
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
@router.get("/api/money-heatmap")
def money_heatmap(top: int = 0):
    """最新交易日资金热力图（treemap 数据）。

    按行业聚合最新交易日 net_mf_amount（万元），返回：
      {trade_date, total_wan, items: [{name, value, net_wan, sector}]}
    value 用绝对值（treemap 面积），net_wan 保留符号（流入红/流出绿）。
    """
    try:
        dates, pivot = _load_sector_flow(1)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="资金流数据不可用")
    if not dates:
        raise HTTPException(status_code=404, detail="无资金流数据")
    trade_date = dates[-1]
    row = pd.to_numeric(pd.Series(pivot.iloc[-1]), errors="coerce").dropna()
    nonzero = row[row != 0]
    ordered = nonzero.reindex(nonzero.abs().sort_values(ascending=False).index)
    selected = ordered if top <= 0 else ordered.head(top)
    items = [
        {
            "name": str(k),
            "value": int(abs(round(float(v), 0))),   # treemap 面积用绝对值
            "net_wan": int(round(float(v), 0)),      # 保留正负号
        }
        for k, v in selected.items()
    ]
    total_wan = int(round(float(row.sum())))
    return {"trade_date": trade_date, "total_wan": total_wan, "items": items}
@router.get("/api/stock/{ts_code}/flow")
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
