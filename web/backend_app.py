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

启动：设置 AB_BACKEND_PORT=8001 后运行 python backend_app.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
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

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scoring import (
    calc_fund_flow_strength,
)
from signals import detect_accumulation_breakout

from ab_screener.api.legacy_state import (
    _DB,
    _BUILD_VERSION,
    _STARTED_AT,
    _INSTANCE_ID,
    _LOGGER,
    _store,
    _SECTOR_FLOW_CACHE,
    _SIG_CACHE,
    _OVERVIEW_CACHE,
    _SCAN_RESULT_CACHE,
    _DATES_CACHE,
    _SCAN_TASKS,
    _SCAN_CANCEL_EVENTS,
    _SCAN_LOCK,
    _SCAN_TASKS_MAX,
    _SECTOR_FLOW_CACHE_MAX,
    _LAB_TASKS,
    _LAB_LOCK,
    _LAB_TASKS_MAX,
    _LAB_STORE,
    _SYNC_LOCK,
    _SYNC_STATE,
    _BT_LOCK,
    _BT_TASKS,
    _BT_TASKS_MAX,
)

if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true":
    raise RuntimeError("LIVE_TRADING_ENABLED 必须保持 false；本项目不包含真实下单能力")

# P0.4 契约接线（P8）：启动时只断言 schema 兼容（绝不自动 DDL），
# 未应用迁移/checksum 漂移 → 拒绝启动（fail-closed）。
from ab_screener.data.schema_check import assert_schema_compatible

assert_schema_compatible(_PARENT / "runtime" / "stock_data.db")

app = FastAPI(title="A股 横盘吸筹→启动 选股系统", version="2.0.0")

# P7.1 装配：v2 routers（与 legacy API 并存；重复 path 由 OpenAPI 测试断言为 0）。
# 本文件已自带 legacy /api/scan 路由，故跳过 scan_router 避免重复 Operation ID。
from ab_screener.api.app_factory import include_v2_routers

include_v2_routers(app, include_scan_router=False)
# G2 拆路由：只读杂项（health/setup-status/manifests/today/release-readiness/kline）
from ab_screener.api.routers.legacy_misc import router as legacy_misc_router

app.include_router(legacy_misc_router)
# G2 拆路由：市场数据（overview/portfolio/stock/sector-flow/money-heatmap/stock-flow）
from ab_screener.api.routers.legacy_market import router as legacy_market_router

app.include_router(legacy_market_router)
# G2 拆路由：扫描（scan worker + scan 5 路由）
from ab_screener.api.routers.legacy_scan import router as legacy_scan_router

app.include_router(legacy_scan_router)

# G2 拆路由：纸面 / 实验室 / 同步 / 回测
from ab_screener.api.routers.legacy_paper import router as legacy_paper_router
from ab_screener.api.routers.legacy_lab import router as legacy_lab_router
from ab_screener.api.routers.legacy_sync import router as legacy_sync_router
from ab_screener.api.routers.legacy_backtest import router as legacy_backtest_router

app.include_router(legacy_paper_router)
app.include_router(legacy_lab_router)
app.include_router(legacy_sync_router)
app.include_router(legacy_backtest_router)
# 2026-08-16 整改：CORS 从 "*" 收敛为本机白名单（单端口 8001 + 开发前端 3001）。
# 本服务只绑 127.0.0.1，但跨源读写在浏览器内即可完成——放开 "*" 等于让任意网页
# 读取持仓/纸面账户并触发扫描。同源请求不需要 CORS，白名单只为 vite 开发代理服务。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:3001",
        "http://localhost:3001",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mount_logic_router() -> None:
    """挂载 logic_platform 路由（延迟 import 防循环；失败仅告警不影响宿主）。"""
    try:
        from logic_platform.api.routes import router as _logic_router

        app.include_router(_logic_router)
        _LOGGER.info("logic_platform router 已挂载 /api/logic")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("logic_platform router 挂载失败: %s", exc)


_mount_logic_router()


def _paper_enabled() -> bool:
    return os.environ.get("PAPER_TRADING_ENABLED", "true").lower() == "true"


@app.middleware("http")
async def paper_feature_gate(request: Request, call_next):
    if (
        request.url.path.startswith("/api/paper")
        and request.url.path != "/api/paper/gates/status"
        and not _paper_enabled()
    ):
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "PAPER_TRADING_DISABLED",
                                 "message": "纸面交易模块已关闭", "details": {},
                                 "retryable": False}},
        )
    return await call_next(request)


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    """防跨站：Host 须为本机主机名、写操作 Origin 须为本机主机名。

    2026-08-16 整改（对应 CORS "*" 漏洞）：绑定 127.0.0.1 不能阻止用户浏览器里
    的恶意网页向本服务发起请求（CSRF / DNS rebinding）。规则：
      - Host 的主机名必须是 127.0.0.1 / localhost / ::1（端口不限——dev 前端
        或本机其它工具端口都放行，外部域名 rebinding 一律拒绝）；
      - 写方法（POST/PUT/PATCH/DELETE）若带 Origin，其主机名同样必须是本机；
      - 不带 Origin 的写请求（curl / Agent 脚本）放行，保持 CLI 兼容。
    """
    import urllib.parse

    def _hostname_of(raw: str) -> str:
        try:
            host = urllib.parse.urlparse(raw if "//" in raw else f"//{raw}").hostname
            return (host or "").lower()
        except (ValueError, AttributeError):
            return ""

    local_hostnames = {"127.0.0.1", "localhost", "::1"}
    # starlette TestClient 默认 Host=testserver：仅测试放行（攻击者无法注册该域名做 rebinding）
    local_hostnames.add("testserver")

    host = request.headers.get("host") or ""
    if host and _hostname_of(host) not in local_hostnames:
        return JSONResponse(status_code=403, content={"detail": "仅允许本机访问"})

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin and _hostname_of(origin) not in local_hostnames:
            return JSONResponse(
                status_code=403,
                content={"detail": "跨站写请求被拒绝（仅允许本机来源）"},
            )
    return await call_next(request)


# ── 模块级单例（schema 初始化只做一次） ──


# ── 小白友好：单端口托管前端 dist（无需再开 npm）──
_DIST = _BASE / "frontend" / "dist"
_HAS_DIST = _DIST.is_dir() and (_DIST / "index.html").is_file()


if _HAS_DIST:
    assets_dir = _DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def _spa_index():
        return FileResponse(_DIST / "index.html")

@app.get("/{full_path:path}")
def _spa_fallback(full_path: str):
    # API 已由上方路由处理；其余走静态或 SPA
    if full_path.startswith("api/") or full_path == "api":
        raise HTTPException(status_code=404, detail="Not Found")
    # 防路径穿越：显式拒绝 .. 与编码变体；解析后必须仍位于 dist 目录内
    if ".." in full_path:
        raise HTTPException(status_code=404, detail="Not Found")
    candidate = _DIST / full_path
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(_DIST.resolve()):
            raise HTTPException(status_code=404, detail="Not Found")
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Not Found")
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(_DIST / "index.html")


# ── 自动日终调度器（阶段5）：交易日 16:15 后轮询，每账户/交易日最多成功一次 ──

def _auto_settle_loop() -> None:
    """后台线程：每日 16:15 后尝试对最近已完成交易日执行日结；幂等（已 DONE 跳过）。"""
    import time as _t
    from zoneinfo import ZoneInfo as _ZI

    tz = _ZI("Asia/Shanghai")
    while True:
        try:
            now = datetime.now(tz)
            latest_local = _store.max_trade_date("daily") or ""
            today = now.strftime("%Y%m%d")
            after_close = now.hour > 16 or (now.hour == 16 and now.minute >= 15)
            # 当天收盘后正常运行；周末/重启时补跑本地最新已完成交易日。
            if latest_local and (after_close or latest_local < today):
                from paper_trading.cal import is_open as _cal_is_open
                from paper_trading.settlement import run_settlement

                try:
                    target = latest_local
                    if _cal_is_open(_DB, target):
                        from ab_screener.data.paper_query import last_done_cycle_date

                        last_done = last_done_cycle_date(_DB)
                        if last_done and last_done >= target:
                            pass  # 已日结，跳过
                        else:
                            try:
                                run_settlement(_DB, target)
                            except Exception as exc:  # noqa: BLE001
                                from tushare_init import sanitize_error
                                _LOGGER.warning("纸面日结待重试 %s: %s", target,
                                                sanitize_error(exc)[:240])
                except Exception as exc:  # noqa: BLE001
                    from tushare_init import sanitize_error
                    _LOGGER.error("纸面调度检查失败: %s", sanitize_error(exc)[:240])
        except Exception as exc:  # noqa: BLE001
            from tushare_init import sanitize_error
            _LOGGER.error("纸面调度循环失败: %s", sanitize_error(exc)[:240])
        _t.sleep(60)  # 每分钟轮询


if _paper_enabled():
    threading.Thread(target=_auto_settle_loop, daemon=True, name="paper-auto-settle").start()


def _backend_port() -> int:
    raw = os.environ.get("AB_BACKEND_PORT", "8001").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("AB_BACKEND_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("AB_BACKEND_PORT must be between 1 and 65535")
    return port




if __name__ == "__main__":
    import uvicorn

    _port = _backend_port()
    print(
        f"UI: http://127.0.0.1:{_port}/  "
        f"(dist={'yes' if _HAS_DIST else 'no-use :3001'})"
    )
    uvicorn.run(app, host="127.0.0.1", port=_backend_port())