# G2 拆分方案 — backend_app.py 拆路由（收尾计划 G2）

> 对应 `docs/IMPROVEMENT-PROGRAM-2026-08-20.md` G2。
> 目标：宿主文件只装配；legacy `/api/*` 迁 `ab_screener/api/routers/`；OpenAPI 无重复 path。

## 现状

`web/backend_app.py` 约 3014 行，混装：

| 区块 | 行数约 | 内容 |
|------|--------|------|
| 装配 + 中间件 | ~200 | app 创建、CORS、本机守卫、schema 检查、logic router 挂载 |
| 数据读取辅助 | ~150 | `_kline_series_for` / `_sig_for` / `_fina_for` / `_load_sector_flow` |
| 扫描任务管理 | ~200 | `_run_scan_worker` / `_new_task` / cancel（子进程 + 进度文件） |
| legacy API | ~1500 | scan / overview / portfolio / paper(20+) / stock / sector-flow / money-heatmap / setup-status / release-readiness |
| 策略实验室 | ~300 | `_LAB_*` / `/api/lab/*`（10+ 端点） |
| 回测工作台 | ~250 | `_BT_*` / `/api/backtest/*` |
| 数据同步 | ~80 | `_SYNC_*` / `/api/sync*` |
| 日结调度 + SPA | ~100 | `_auto_settle_loop` / `/` / `/{full_path}` |

共享模块级状态（拆分难点）：`_store`、`_DB`、`_BUILD_VERSION`、5 组缓存、`_SCAN_TASKS`/`_LAB_TASKS`/`_BT_TASKS`/`_SYNC_STATE` + 各自锁。

## 拆分方案（分 5 步，每步全量测试验证）

1. **共享状态模块** `ab_screener/api/legacy_state.py`
   - 集中 `_store`、`_DB`、`_BUILD_VERSION`、`_STARTED_AT`、`_INSTANCE_ID` 及全部缓存/任务字典/锁。
   - backend_app.py 改为 `from ab_screener.api.legacy_state import ...`（零行为变化）。
   - 验证：`pytest -k "not browser"` 全绿。

2. **只读路由先行** `ab_screener/api/routers/legacy_misc.py`
   - 迁 `/api/setup-status`、`/api/kline/{ts_code}`、`/api/manifests`、`/api/today`、`/api/release/readiness`（依赖 `_store`/`_DB`，无后台线程状态）。
   - backend_app.py 删这些函数 + `include_router`。
   - 验证：`test_openapi_contract_v2.py` + 相关路由测试绿，无重复 path。

3. **扫描 + 总览 + 股票** `legacy_scan.py` / `legacy_market.py`
   - 迁 `/api/scan*`、`/api/overview`、`/api/stock/*`、`/api/sector-flow`、`/api/money-heatmap`、`/api/portfolio`、`/api/health`。

4. **纸面 + 实验室 + 回测 + 同步** `legacy_paper.py` / `legacy_lab.py` / `legacy_backtest.py` / `legacy_sync.py`
   - 迁 `/api/paper/*`、`/api/lab/*`、`/api/backtest/*`、`/api/sync*`。

5. **宿主收口**
   - backend_app.py 只保留：app 创建、中间件、SPA 托管、`include_router` 装配、`if __name__`。
   - 验证：三命令（pytest + arch --strict + tsc）。

## 风险与前置

- **git objects 损坏**：无 commit/回滚保护。每步必须先全量测试绿再进下一步；任一失败立即停止并回退该步改动（文件级手工回退）。
- **循环 import**：`legacy_state` 只依赖根模块（`local_store`/`signals`/`build_version`），不依赖 `backend_app`；router 依赖 `legacy_state` + `app`（延迟 import 装配）。
- **共享可变状态语义**：扫描/lab/回测/sync 的后台线程必须保持同一份全局状态，迁移时状态对象引用不变（只搬家，不复制）。
