"""市场情报 API（P7.1）：档案/时间线/事件/宽度/数据状态（只读，传 snapshot/decision_at）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from ab_screener.intelligence.breadth import market_breadth
from ab_screener.intelligence.catalog import search_stocks, stock_catalog
from ab_screener.intelligence.desk_supplement import build_desk_supplement
from ab_screener.intelligence.indices import index_snapshot
from ab_screener.intelligence.limit_up import limit_up_ladder
from ab_screener.intelligence.quality import data_source_status
from ab_screener.intelligence.timeline import corporate_action_timeline

router = APIRouter(prefix="/api/v2/intelligence", tags=["intelligence"])


@router.get("/search")
def search(q: str, db_path: str = Depends(get_db_path)) -> list[dict[str, Any]]:
    """代码/名称/行业搜索（简化：ts_code/name 前缀匹配）。"""
    return search_stocks(db_path, q)


@router.get("/stocks/{ts_code}")
def stock_profile(ts_code: str, db_path: str = Depends(get_db_path), decision_at: str | None = None) -> dict[str, Any]:
    """指定 as-of 的个股档案（decision_at 可选）。"""
    profile = stock_catalog(db_path, ts_code)
    if profile["instrument"] is None and profile["latest_bar"] is None:
        raise HTTPException(status_code=404, detail="标的无档案数据")
    return profile


@router.get("/stocks/{ts_code}/timeline")
def stock_timeline(ts_code: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    """公告/公司行为时间线（PIT available_at）。"""
    events = [e.to_dict() for e in corporate_action_timeline(db_path, ts_code)]
    return {"ts_code": ts_code, "events": events, "count": len(events)}


@router.get("/breadth")
def breadth(trade_date: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    return market_breadth(db_path, trade_date)


@router.get("/data-status")
def data_status(db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    return data_source_status(db_path)


@router.get("/desk-supplement")
def desk_supplement(
    trade_date: str | None = None,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """指挥舱情报补充（astock 口径，只读）：宽度 + 涨停梯队 + 七指数 + 可选 HTTP 全球行情。"""
    return build_desk_supplement(db_path, trade_date)


@router.get("/limit-up")
def limit_up(
    trade_date: str,
    top_n: int = 20,
    db_path: str = Depends(get_db_path),
) -> dict[str, Any]:
    """涨停/跌停梯队（只读，20%/10% 板宽口径）。"""
    n = max(1, min(int(top_n), 20))
    return limit_up_ladder(db_path, trade_date, top_n=n)


@router.get("/indices")
def indices(trade_date: str, db_path: str = Depends(get_db_path)) -> dict[str, Any]:
    """A 股七指数快照（只读，本地 daily）。"""
    return index_snapshot(db_path, trade_date)
