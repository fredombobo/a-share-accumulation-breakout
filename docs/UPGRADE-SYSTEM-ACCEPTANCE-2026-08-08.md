# upgrade system 验收报告

> 本文是升级中期记录，已由 `docs/FINAL-ACCEPTANCE-2026-08-08.md` 取代；其中 403 日、degraded、未重跑真实门禁等状态不再代表当前系统。

依据：`upgarde system.md`（仓库根目录，文件名历史拼写）  
仓库：`E:\CODEX\Stock_selection\accumulation_breakout`  
日期：2026-08-08

---

## 0. 执行范围说明

本轮**严格按六阶段计划**推进治理升级，并与既有纸面交易（PHASE0–8）并存：

| 计划模块 | 本轮交付 |
|----------|----------|
| `ab_screener/domain` | ✅ 成本引擎、StrategyProfile、fail-closed 晋级门禁 |
| `ab_screener/data` | ✅ Repository、Parquet 缓存、v9 治理表迁移 |
| `ab_screener/research` | ✅ 随机/均线基线骨架 |
| `ab_screener/application` | ✅ 持久 `scan_jobs` |
| `ab_screener/api` | ✅ scan_router + app_factory（与 legacy 并存） |
| `ab_screener/jobs` | ✅ 独立 Worker 入口 |
| 关闭 pickle 读取 | ✅ `load_market_data` 不再 `read_pickle` |
| 纸面交易回归 | ✅ 全量 pytest 未破坏 |

**未宣称完成（fail-closed）**：本地日线仍 **403 日**、`research_mode=degraded`，**不得**宣称 full edge / 可下单参数。历史扩至 720+ 依赖有效 `TUSHARE_TOKEN` 执行 `sync_history.py`。

---

## 1. 阶段对照验收

### 阶段 1：可信度止血与历史扩容

| 项 | 结果 | 证据 |
|----|------|------|
| 备份 SQLite / portfolio / dist | ✅ | `runtime/backups/upgrade_20260808_095238/` |
| 治理表迁移 v9 | ✅ | schema_version max=**9**；表 `dataset_partitions/scan_jobs/scan_runs/...` |
| degraded fail-closed | ✅ | `research_gate.assert_no_edge_claim` + `can_promote_profile` 测试 |
| 历史 720+ 日 | ❌ 阻塞 | `research_status`：403 日，mode=**degraded** |
| 扫描/研究元数据钩子 | 🟡 部分 | `/api/scan` 返回 `config_hash/as_of/run_id`；`scan_runs` 表已建，写入链路待 Worker 全量固化 |

**门禁**：`research_status=full` 未达成 → **系统继续 fail-closed**，禁止 profile 晋级 active 新参数。

### 阶段 2：领域包、Repository、Parquet

| 项 | 结果 |
|----|------|
| `ab_screener` 包结构 | ✅ |
| SQLite repository 按需加载 | ✅ `MarketRepository.load_daily` 强制过滤 |
| Parquet 派生缓存 | ✅ `parquet_cache.load_daily_cached`（无 pyarrow 时回退 SQLite） |
| 关闭 pickle 读取 | ✅ 源码门禁 `test_load_market_data_no_pickle` |
| 旧 pkl 不删除 | ✅ 仅不读取 |
| 影子比较旧/新扫描 | 🟡 未跑全市场影子（耗时）；接口层兼容 |

### 阶段 3：持久任务、Worker、性能

| 项 | 结果 |
|----|------|
| `scan_jobs` 持久化 | ✅ |
| 独立 Worker | ✅ `python -m ab_screener.jobs.scan_worker` |
| 扫描子进程可杀 | ✅ 既有 `scan_job_runner` + taskkill 树 |
| 向量化预筛 | ✅ `prefilter_fast.volume_breakout_candidates` |
| 取消 ≤3s | ✅ 子进程 kill 路径（需新后端进程；旧 8000 僵尸 PID 除外） |
| API 不因扫描退出 | ✅ 子进程隔离 |

### 阶段 4：路由/配置/可信回测

| 项 | 结果 |
|----|------|
| Router 拆分 | 🟡 `ab_screener.api.scan_router` 已备；`backend_app` 仍为兼容壳（行数 >200） |
| StrategyProfile JSON | ✅ `configs/default_strategy_profile.json` |
| 成本/滑点/涨跌停/整手 | ✅ `ab_screener.domain.costs` + 单测 |
| 基线 | ✅ 随机种子 `20260808`、MA20/60 |
| 晋级仅 full | ✅ `can_promote_profile` |
| 37 API 兼容 | ✅ 原路径保留；scan 响应增可选字段 |

### 阶段 5：前端研究终端

| 项 | 结果 |
|----|------|
| Lab 研究隔离 | ✅ 既有文案 + catalog |
| 懒加载/路由拆分 | ✅ Overview/Lab/Paper/EChart 分包（build 已拆） |
| ECharts 主包体积 | 🟡 `EChart-*.js` 仍 ~1.1MB（超过「单资源 800KB」目标） |
| 纸面「不向券商下单」 | ✅ 既有 banner |

### 阶段 6：全链路门禁

见下节实测。

---

## 2. 质量门禁实测

```text
python -m pytest
  → 163 passed, 92 warnings  (~55s)

python -m ruff check ab_screener prefilter_fast.py
  → All checks passed (autofix applied)

python -m mypy ab_screener --follow-imports=skip
  → Success: 20 source files

npm --prefix web/frontend run build
  → success；页面 chunk 拆分；EChart 仍大

python -m paper_trading.real_data_gate --days 730
  → 以环境 Token/库覆盖为准（既有 PHASE8 报告可参考；本轮未强制重跑）
```

新增测试：`tests/test_upgrade_system.py`（11 项）覆盖迁移、成本、预筛、fail-closed、scan_jobs、无 pickle。

---

## 3. 回滚开关（与计划对齐）

| 变量 | 默认 | 含义 |
|------|------|------|
| `SCANNER_ENGINE` | `v2` | legacy\|v2（health 暴露） |
| `MARKET_CACHE_MODE` | `parquet` | off\|parquet |
| `SCAN_WORKER_ENABLED` | `true` | 独立 worker 语义 |
| `LIVE_TRADING_ENABLED` | `false` | **恒 false** |
| `PAPER_TRADING_ENABLED` | `true` | 纸面模块 |

回滚到旧扫描行为：停止服务，恢复 `runtime/backups/upgrade_*` 的 db/dist，并设置 `MARKET_CACHE_MODE=off`（禁止 pickle 回潮）。

---

## 4. 与「最终完成条件」逐条对照

| 条件 | 状态 |
|------|------|
| 本地历史 full 门槛 | ❌ 403 日 degraded |
| SQLite 唯一事实源，pickle 不参与 | ✅ 读取路径关闭 |
| 扫描可持久/恢复/取消且不影响 API | 🟡 持久表+子进程取消已落地；崩溃恢复需 worker 长跑观察 |
| 回测无同收盘成交；成本可复算 | ✅ 成本引擎口径固定 |
| 配置/数据/代码/结果哈希可复现 | 🟡 config_hash 已有；result_hash 全链路写入未完 |
| 全部质量门禁 + 测试通过 | ✅ pytest/ruff/mypy(ab_screener)/build |
| 无真实券商下单 | ✅ |

**本轮验收结论：架构与止血项达标（CONDITIONAL PASS）；full 研究门禁未达标，系统 fail-closed。**

---

## 5. 后续必须项（计划内未关闭）

1. 有效 Token 下 `python sync_history.py` → `research_status` **mode=full**  
2. 将 `backend_app.py` 收缩为 app factory（≤200 行）并默认挂载 v2 routers  
3. Worker supervisor 启动器（报告 8000 占用、不误杀）  
4. 扫描漏斗写入 `scan_run_candidates` + 前端 Dashboard 漏斗  
5. ECharts 进一步 tree-shake / 按需 chart 导入以压到 800KB 下  
6. 连续 ≥5 交易日观察任务恢复与日结  

---

## 6. 关键路径速查

| 路径 | 用途 |
|------|------|
| `upgarde system.md` | 本计划原文 |
| `ab_screener/` | v2 领域包 |
| `configs/default_strategy_profile.json` | 默认策略档案 |
| `python -m ab_screener.jobs.scan_worker` | 扫描 Worker |
| `runtime/backups/upgrade_*` | 本轮备份 |
| `tests/test_upgrade_system.py` | 升级验收测试 |
