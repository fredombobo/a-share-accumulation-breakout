"""/api/logic 路由（Phase 0：health；Phase 1：features / explain）。

挂在宿主 FastAPI 上（web/backend_app.py include_router）。
所有响应带 as_of / research_only；数据层全部降级不崩。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from logic_platform import FEATURE_VERSION
from logic_platform.config import get_config
from logic_platform.data.ab_store import ABStore
from logic_platform.data.lake_bridge import LakeBridge
from logic_platform.data.migrations import schema_version

_LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logic", tags=["logic"])

# 模块级单例（对齐宿主 backend_app._store 模式）
_store: ABStore | None = None
_bridge: LakeBridge | None = None


def _get_store() -> ABStore:
    global _store
    if _store is None:
        _store = ABStore()
    return _store


def _get_bridge() -> LakeBridge:
    global _bridge
    if _bridge is None:
        _bridge = LakeBridge()
    return _bridge


@router.get("/health")
def logic_health() -> dict:
    """功能开关、lake 可见性、schema 版本、数据新鲜度。"""
    cfg = get_config()
    lake = _get_bridge().status()
    sv = schema_version(_get_store().db_path)
    as_of = _get_store().latest_trade_date()
    return {
        "enabled": cfg.enabled,
        "lake": lake,
        "schema_version": sv,
        "feature_version": FEATURE_VERSION,
        "research_only": cfg.research_only_default,
        "as_of": as_of,
    }


@router.get("/features/{ts_code}")
def logic_features(ts_code: str) -> dict:
    """近窗特征 + 状态序列（研究信号，非买卖建议）。"""
    from logic_platform.service import analyze

    payload = analyze(ts_code, _get_store())
    if payload is None:
        return {
            "ts_code": ts_code,
            "error": "no_data",
            "message": "本地库无该标的日线数据",
            "research_only": True,
        }
    return payload


@router.get("/explain/{ts_code}")
def logic_explain(ts_code: str) -> dict:
    """单股人话解释：为何处于该状态（docs 附录 A）。"""
    from logic_platform.service import explain

    payload = explain(ts_code, _get_store())
    if payload is None:
        return {
            "ts_code": ts_code,
            "error": "no_data",
            "message": "本地库无该标的日线数据",
            "research_only": True,
        }
    return payload


@router.post("/predict")
def logic_predict(body: dict) -> dict:
    """批量推理：{ts_codes: [...], horizon: 10} → 预测列表。

    模型未训练时返回 warning（不报错）——research 语义，非买卖建议。
    """
    from logic_platform.service import predict_batch

    codes = body.get("ts_codes") or []
    if not codes or not isinstance(codes, list):
        return {"error": "bad_request", "message": "需要 ts_codes 列表",
                "research_only": True}
    return predict_batch([str(c).strip() for c in codes][:50], _get_store())


@router.post("/backtest")
def logic_run_backtest(body: dict) -> dict:
    """同步执行回测闭环：{template, max_codes?, step?, set: ["exit.stop_pct=0.08"], gates: ["min_trades=20"]}。

    长任务风险控制：max_codes ≤ 200、step ≥ 5（约 1~4 分钟）；
    超大规模请用 CLI（run_logic_backtest --max-codes 2000）。
    """
    from logic_platform.backtest.pipeline import run_pipeline

    template = str(body.get("template") or "vol_breakout_v1")
    max_codes = min(int(body.get("max_codes") or 100), 200)
    step = max(int(body.get("step") or 10), 5)
    set_ov = [str(x) for x in (body.get("set") or [])]
    gate_ov = [str(x) for x in (body.get("gates") or [])]

    result = run_pipeline(
        template,
        params_overrides={"max_codes": max_codes, "step": step},
        set_overrides=set_ov,
        gate_overrides=gate_ov,
    )
    result["research_only"] = True
    return result


@router.get("/strategies")
def logic_strategies_list() -> dict:
    """策略库列表（含闸门状态与最近回测摘要）。"""
    from logic_platform.data.strategy_repo import list_strategies

    items = list_strategies(_get_store().db_path)
    return {"strategies": items, "count": len(items), "research_only": True}


@router.get("/strategies/{strategy_id}")
def logic_strategies_detail(strategy_id: str) -> dict:
    """策略详情：DSL + 全部回测 + 闸门。"""
    from logic_platform.data.strategy_repo import get_strategy

    item = get_strategy(_get_store().db_path, strategy_id)
    if item is None:
        return {"error": "not_found", "message": f"策略不存在: {strategy_id}",
                "research_only": True}
    return {"strategy": item, "research_only": True}


@router.get("/backtest/{run_id}")
def logic_backtest_detail(run_id: str) -> dict:
    """单次回测详情。"""
    from logic_platform.data.strategy_repo import get_backtest

    item = get_backtest(_get_store().db_path, run_id)
    if item is None:
        return {"error": "not_found", "message": f"回测不存在: {run_id}",
                "research_only": True}
    return {"backtest": item, "research_only": True}
