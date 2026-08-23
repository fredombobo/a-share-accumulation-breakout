"""legacy 只读杂项路由（G2 拆路由第 2 步）。

迁自 `web/backend_app.py` 的无后台线程状态路由：
- `/api/health`            健康检查（含数据新鲜度/市场状态/扫描心跳）
- `/api/release/readiness` 发布就绪判定
- `/api/setup-status`      新手向导就绪状态
- `/api/manifests` / `/api/today` / `/api/manifests/{trade_date}`  每日运行清单与今日唯一动作
- `/api/kline/{ts_code}`   通用 K 线查询

依赖：共享状态从 `ab_screener.api.legacy_state` import；路径/端口变量在本模块
重新计算（与 backend_app 同值，不互相 import，避免循环）。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ab_screener.api.deps import get_db_path
from ab_screener.api.legacy_state import (
    _BUILD_VERSION,
    _DB,
    _INSTANCE_ID,
    _STARTED_AT,
    _store,
)

router = APIRouter(tags=["legacy"])

_PARENT = Path(__file__).resolve().parents[3]  # routers → api → ab_screener → 项目根
_BASE = _PARENT / "web"
_DIST = _BASE / "frontend" / "dist"
_HAS_DIST = _DIST.is_dir() and (_DIST / "index.html").is_file()


def _backend_port() -> int:
    raw = os.environ.get("AB_BACKEND_PORT", "8001").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("AB_BACKEND_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("AB_BACKEND_PORT must be between 1 and 65535")
    return port


@router.get("/api/health")
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
    # upgrade system 扩展字段（可选，保持兼容）
    schema_ver = None
    research_mode = None
    worker_hb = None
    try:
        from ab_screener.data.scan_run_repository import active_scan_worker, schema_max_version

        schema_ver = schema_max_version(_store.db_path)
        worker_hb = active_scan_worker(_store.db_path)
    except Exception:  # noqa: BLE001
        pass
    try:
        from research_windows import recommend_research_plan

        research_mode = recommend_research_plan().mode
    except Exception:  # noqa: BLE001
        research_mode = None
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "as_of": as_of,
        "freshness": fresh,
        "regime": reg,
        "build_version": _BUILD_VERSION,
        "started_at": _STARTED_AT,
        "instance_id": _INSTANCE_ID,
        "schema_version": schema_ver,
        "research_mode": research_mode,
        # 实际执行路径：内存任务 + scan_job_runner 子进程 + scan_jobs 双写
        "scanner_engine": os.environ.get("SCANNER_ENGINE", "subprocess_v2"),
        "market_cache_mode": os.environ.get("MARKET_CACHE_MODE", "parquet"),
        "scan_worker": worker_hb,
        "live_trading_enabled": False,
        "guided_ui_enabled": os.environ.get("GUIDED_UI_ENABLED", "true").lower()
        not in {"0", "false", "no", "off"},
        "pickle_read_enabled": False,
    }


@router.get("/api/release/readiness")
def release_readiness():
    """当前代码、配置、数据库与 24 小时真实门禁的联合发布判定。"""
    from ab_screener.application.release_evidence import build_release_evidence

    return build_release_evidence(_BASE, _DB)


@router.get("/api/setup-status")
def setup_status():
    """新手向导用：Token / 数据 / 扫描是否就绪。"""
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    env_path = _PARENT / ".env"
    if not token and env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("TUSHARE_TOKEN=") and not line.startswith("#"):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:  # noqa: BLE001
            pass
    latest_daily = _store.max_trade_date("daily")
    latest_mf = _store.max_trade_date("moneyflow")
    scan_n = 0
    try:
        df_scan = _store.load_scan_result()
        scan_n = 0 if df_scan is None or getattr(df_scan, "empty", True) else len(df_scan)
    except Exception:  # noqa: BLE001
        scan_n = 0
    return {
        "has_token": bool(token) and token not in ("your_token_here", "changeme"),
        "has_frontend_dist": _HAS_DIST,
        "latest_daily": latest_daily,
        "latest_moneyflow": latest_mf,
        "has_market_data": bool(latest_daily),
        "scan_result_rows": scan_n,
        "ui_mode": "single_port" if _HAS_DIST else "dev_split",
        "open_url": f"http://127.0.0.1:{_backend_port()}/" if _HAS_DIST else "http://127.0.0.1:3001/",
        "tips": [
            "没有 Token：编辑项目根目录 .env 填入 TUSHARE_TOKEN",
            "没有行情：双击「一键启动.bat」会自动同步，或点界面「扫描」",
            "A 池为空且提示防守：市场弱，系统故意不开新仓，属正常",
        ],
    }


@router.get("/api/manifests")
def daily_manifests(limit: int = 30):
    """List immutable cross-domain daily run evidence."""
    from ab_screener.application.daily_manifest import list_daily_manifests

    return {"items": list_daily_manifests(_DB, limit=limit)}


@router.get("/api/today")
def today_guide(
    at: str | None = None,
    db_path: str = Depends(get_db_path),
) -> dict[str, object]:
    """Return exactly one plain-language action for the current workflow state."""
    from ab_screener.application.today_guide import build_today_guide

    try:
        now = datetime.fromisoformat(at) if at else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="at 必须是 ISO 8601 时间") from exc
    return build_today_guide(db_path, now=now)


@router.get("/api/manifests/{trade_date}")
def daily_manifest_detail(trade_date: str):
    from ab_screener.application.daily_manifest import get_daily_manifest

    manifest = get_daily_manifest(_DB, trade_date)
    if manifest is None:
        raise HTTPException(status_code=404, detail="该交易日尚无运行清单")
    return manifest


@router.get("/api/kline/{ts_code}")
def kline_range(ts_code: str, start: str | None = None, end: str | None = None, limit: int = 180):
    """通用 K 线查询（回测工作台交易 K 线展示用）。

    start/end: YYYYMMDD；不传 end 时取最新 limit 根。返回升序 kline。
    """
    from local_store import LocalStore

    code = ts_code.upper()
    if not code.endswith((".SH", ".SZ", ".BJ")):
        raise HTTPException(status_code=422, detail="ts_code 需为 000001.SZ 形式")
    limit = max(20, min(int(limit), 400))
    try:
        df = LocalStore().load_daily(ts_codes=[code], start=start, end=end)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"K线查询失败: {exc}") from exc
    if df is None or df.empty:
        return {"ts_code": code, "kline": []}
    df = df.sort_values("trade_date").tail(limit)
    kline = []
    for row in df.itertuples(index=False):
        kline.append({
            "trade_date": str(row.trade_date),
            "open": float(row.open) if row.open is not None else None,
            "high": float(row.high) if row.high is not None else None,
            "low": float(row.low) if row.low is not None else None,
            "close": float(row.close) if row.close is not None else None,
            "vol": float(row.vol) if row.vol is not None else None,
        })
    return {"ts_code": code, "kline": kline}
