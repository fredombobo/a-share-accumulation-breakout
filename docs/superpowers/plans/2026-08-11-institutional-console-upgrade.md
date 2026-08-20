# 机构级选股控制台升级计划（Agent 可执行版）

| 字段 | 内容 |
|------|------|
| 文档 ID | `INSTITUTIONAL-CONSOLE-UPGRADE-2026-08-11` |
| 版本 | **v1.1**（2026-08-16 对标机构级控制台修订） |
| 状态 | **待执行**（分阶段实现） |
| 宿主仓库 | `E:\CODEX\Stock_selection\accumulation_breakout` |
| 制定日期 | 2026-08-11（v1） · 2026-08-16（v1.1 机构级对标升级） |
| 读者 | 实现 Agent / 人类审查者 |
| 前置基线 | 九分闭环已验收；ENTRY-DEFINITION-V1 已冻结；纸交易/Lab/扫描/回测工作台已可用 |
| 相关文档 | `docs/STATUS.md` · `docs/ENTRY-DEFINITION-V1.md` · `docs/RESEARCH-ROADMAP.md` · `AGENTS.md` |

---

## 0A. v1.1 修订说明（2026-08-16 对标机构级控制台）

> 本版按 Bloomberg / Wind / iFinD 投研终端、私募 PMS 与券商研究所工作台的
> 能力矩阵逐项对标，补齐 v1 缺失的机构级能力与 4 处过时基线。改动摘要：

**过时基线修正（实现 Agent 必须按 v1.1 执行）**：
1. **端口**：AB API 已迁 **8001**（8000 固定留给 AETF Alpha；前端单端口托管）。§1.3-9、P0-A4、P5-A7 全部以 8001 为准。
2. **回测工作台已上线**（`/backtest`，含逐笔 K 线、试验历史），§2.1/附录 B 已一致，缺口清单不再包含。
3. **backend_app 拆分骨架已存在**（`ab_screener/api/app_factory.py` + `scan_router.py`），P0.3 改为「利用现有骨架迁 1–2 个只读路由验证」。
4. **CI 已建立**（`.github/workflows/ci.yml`：ruff + mypy + pytest + 前端构建），P5.4 引用即可。
5. **近期正确性修复已落地**：回测宇宙含退市股（幸存者偏差消除）、纸面参与率单位修复、扫描快照 prune、Lab 多重比较披露——P1 漏斗与 P2 信号实例设计必须与这些新基线对齐。

**新增机构级能力（对标缺口，见 §2.3 矩阵）**：
- P1 增补：市场宽度/广度指标（breadth）、funnel 携带 config_hash 数据血缘
- P2 增补：信号绩效前瞻回填（signal_outcomes 滚动胜率）、操作审计表、全局搜索
- P3 增补：多股对比 `/compare` 提级（原「延后」）、组合 VaR/Sharpe/TWR
- P4 增补：研究假设登记（idea ledger）与人工决策日志
- P5 增补：统一每日调度器（扫描/同步/日结/信号回填编排）、数据库备份策略、数据血缘审计

---

## 0. 给实现 Agent 的一句话任务

把现有「形态选股 + 研究 Lab + 纸交易」产品，升级为**机构式选股控制台工作流**：

**市场指挥舱 → 可版本化扫描方案 → 信号生命周期监控/预警 → 组合约束与暴露 → 复盘归因产品化 → 平台审计/导出**

**禁止**：券商实盘、把 Lab 结果当买卖指令、破坏 fail-closed 研究门禁、静默改动 `ENTRY-DEFINITION-V1` 语义。

---

## 1. 目标与非目标

### 1.1 目标（要达成）

1. **工作流完整**：盘前态势 → 扫描方案 → 候选池 → 监控 → 纸面约束 → 复盘，一屏可导航。
2. **方案可复现**：扫描/研究参数有版本、run_id、指纹；历史可对比。
3. **信号可管理**：候选不是静态表，而是带状态机的生命周期对象。
4. **组合有边界**：单票/行业(主题)/持仓数硬约束，下单前拦截。
5. **复盘可产品化**：假突破归因、按 regime 切片、周报模板，不依赖 CLI 专家操作。
6. **工程可维护**：后端路由拆分；新能力进 `ab_screener/` 包；测试与验收文档齐全。

### 1.2 非目标（明确不做）

| 不做 | 原因 |
|------|------|
| 券商实盘 / OMS 真下单 | 合规与产品边界；`LIVE_TRADING_ENABLED=false` 保持 |
| 全市场 Level-2 实时撮合 | 数据成本与复杂度 |
| 静默修改 ENTRY v1 入场语义 | 必须升 v2 并双写文档 |
| Lab PASS 自动灌入 A 池或自动下单 | 两区隔离强制 |
| 多用户 SaaS / 云部署 | 本阶段仍本地单机 |
| 替换 Tushare 数据源 | 沿用现有栈 |

### 1.3 强制继承约束（所有阶段）

摘自 `AGENTS.md`，违反即验收失败：

1. Tushare 仅 `tushare_init` + curl_cffi；禁裸 requests 直连。
2. 禁全市场 `fina_indicator` 循环。
3. SQLite：每操作新连接；`ON CONFLICT DO UPDATE`；禁 `INSERT OR REPLACE`。
4. 推荐/扫描交付前核对 `as_of` / 数据新鲜度。
5. 资金流单位：万元；勿重复缩放。
6. 突破日日期格式与 ECharts 轴 normalize。
7. 研究：`research_mode!=full` 禁止 edge 话术；晋级 fail-closed。
8. 金额域：纸面用整数分/定点；勿用浮点前端重算账本。
9. 端口约定：AB API **8001**、前端单端口托管（8001 自带 dist）；8000 固定留给 AETF Alpha（勿抢）；FinAgent 已迁 8010。

### 1.4 入场定义（冻结）

- ID：`A_POOL_STRICT_NEXT_OPEN_V1`
- 文档：`docs/ENTRY-DEFINITION-V1.md`
- 代码：`ab_screener/domain/entry_definition.py`
- 任何扫描/回测/归因/纸面入场必须引用该模块；禁止「采样日+1」。

---

## 2. 基线现状（实现前必读）

### 2.1 已具备（勿重复造）

| 能力 | 位置 / 证据 |
|------|-------------|
| 形态选股 + A/B 池 | `signals.py` · `run_screener.py` · `/api/overview` |
| 异步扫描 + 持久 job | `scan_jobs` · `POST /api/scan` |
| 策略 Lab + 可信报告 | `/lab` · `research_runs` · trusted gate |
| 纸交易全闭环 | `paper_trading/` · `/paper` |
| 入场定义 v1 | `entry_definition.py` |
| 假突破归因 CLI | `run_attribution.py` · `ab_screener/research/attribution.py` |
| 证据报告 CLI | `run_evidence_report.py` |
| 九分闭环 | `docs/NINE-POINT-CLOSED-LOOP-ACCEPTANCE-2026-08-11.md` |
| 今日建议 / 发布门禁 | `GET /api/today` · `GET /api/release/readiness` |
| 前端路由 | `/` Overview · `/stock/:tsCode` · `/lab` · `/backtest` · `/paper` |

### 2.2 主要缺口（本计划补齐）

1. 机构式 **信息架构**（指挥舱 / 监控 / 复盘 / 方案库）
2. **扫描方案版本库** 与漏斗审计产品化
3. **信号生命周期** 与告警中心
4. **组合约束引擎** 与暴露看板
5. 归因/证据 **UI 化**（减少 CLI）
6. `backend_app.py` **路由拆分**（工程债）

### 2.3 机构级对标矩阵（v1.1 新增：Bloomberg / Wind / iFinD / 私募 PMS / 券商投研）

对标机构终端与投研工作台的能力矩阵，逐项标注「已有 / 本计划 / 缺失」。
**带 ★ 为本版升级新增项**，已排入对应阶段。

| 域 | 机构能力 | 现状 | 处置 |
|----|---------|------|------|
| **市场态势** | 指数 + 板块轮动 + 市场宽度（涨跌家数/新高新低/站上均线比例） | 仅 000300 指数 MA20/20 日涨幅 | **★ P1 指挥舱补市场宽度指标**（daily 表聚合，零新数据源） |
| | 资金流全景（主力/北向/两融/大宗） | 主力资金流（个股+板块） | 保持；北向/两融列 P4+ 可选扩展 |
| **数据质量** | PIT / 复权 / 缺失监控 / 来源审计 | 行情 PIT 元数据、数据门禁 | 保持；★ P1 funnel 增加 `config_hash` 数据血缘 |
| **研究** | 因子分析 IC/IR/衰减 | 信号评分 + 归因 | ★ P2 信号绩效前瞻回填（signal_outcomes 滚动胜率 = 信号 IC 的个人版） |
| | 假设登记（idea ledger） | 回测工作台试验历史（雏形） | **★ P4 推广为通用研究假设登记** |
| | 防过拟合（PBO/CSCV） | 披露 + fail-closed 门禁 | 保持披露；PBO 列深度工程可选 |
| **组合** | 约束引擎 + 暴露 + 归因 | 纸面风险参数 + 账本 | P3 扩展；★ 加 VaR/Sharpe/TWR |
| | 绩效归因（Brinson） | 无 | 可选深度扩展（P4+，非本版强制） |
| **监控** | 告警中心 + 信号全生命周期 | 无（本计划 P2） | P2 按计划 |
| | 操作审计（谁/何时/改了什么） | 无 | **★ P2/P3 新增 audit_log 表**（方案修改/信号转移/下单/设置变更全记录） |
| **搜索** | 全局搜索（代码/名称/拼音） | 仅代码搜索 | **★ P2 全局搜索提级**（stock_basic 建名称/拼音索引，零外部依赖） |
| **对比** | 多股对比 | 无 | **★ P3 提级**（原「延后」；复用 /api/stock 数据，性价比高） |
| **事件** | 公告/财报/解禁/分红日历 | 无 | P4+ 可选扩展（tushare 接口可得） |
| **运营** | 任务调度编排 | 各任务独立（纸面有 60s 循环） | **★ P5 统一每日调度器**（同步→宽度→扫描→信号回填→日结 单编排，带依赖与失败重试） |
| | 备份/恢复 | 无 | **★ P5 runtime DB 每日备份保留 N 份** |
| | 系统健康（任务延迟/磁盘/DB） | 健康端点 | P5 `/system` 页纳入 |
| **输出** | 一键周报 PDF/MD | 归因/证据 CLI 输出 MD | P4 保留 MD/JSON；PDF 可选扩展 |

**对标结论**：本计划已覆盖机构控制台约 **70%** 的核心骨架；v1.1 补入的 ★ 项
（宽度指标、信号绩效、审计、全局搜索、对比、调度器、备份、血缘）覆盖到约
**85%**；剩余 15%（北向/两融、事件日历、Brinson 归因、PBO、PDF、多用户权限）
明确列入「非本版范围」，留待数据源与时间允许时扩展。

---

## 3. 目标信息架构

```
侧栏（固定顺序，禁止打乱两区语义）：
  1. 指挥舱 Desk          → /desk 或增强 /
  2. 扫描器 Screener      → / 或 /screener（方案+结果）
  3. 监控 Monitor         → /monitor（信号生命周期+预警）
  4. 研究 Research        → /stock/:code（增强）+ 对比 /compare
  5. 组合 Portfolio       → /paper（增强约束/暴露）或 /portfolio
  6. 实验室 Lab           → /lab（保持「非下单」顶栏）
  7. 复盘 Review          → /review
  8. 系统 System          → /system（任务/数据/导出/版本）

顶栏全局状态条（所有页可见）：
  as_of | 数据新鲜度 | 市场 regime | 开仓开关 | 未读告警数 | 构建版本
```

**两区隔离文案（强制 UI 展示）**：

| 区 | 页面 | 可否当「明天买谁」 |
|----|------|-------------------|
| 可交易研究 | 总览 A 池 / 监控可交易态 | 仅候选，仍需人工 |
| 参数/逻辑研究 | `/lab` `/backtest` `/review` 研究报表 | **否** |

---

## 4. 阶段总览

| 阶段 | 名称 | 预估 | 依赖 | 交付物摘要 |
|------|------|------|------|------------|
| **P0** | 基线冻结与 Agent 护栏 | 0.5–1 天 | 无 | 验收模板、路由拆分骨架、不回归 |
| **P1** | 指挥舱 + 方案库 + 漏斗 | 1–1.5 周 | P0 | Desk、ScanProfile、漏斗 API/UI |
| **P2** | 信号生命周期 + 预警 | 1–1.5 周 | P1 | SignalState、Monitor、Alerts |
| **P3** | 组合约束 + 暴露 | 1 周 | P0（可与 P2 并行后半） | Constraints、Exposure、下单拦截 |
| **P4** | 复盘产品化 | 1 周 | P1–P2 | Review UI、归因/证据 API |
| **P5** | 平台硬化 | 0.5–1 周 | P1–P4 | 拆路由完成、导出、CI、STATUS |

**推荐串行**：P0 → P1 → P2 → P4；**P3 可在 P1 完成后与 P2 并行**（不同域）。  
**总工期**：约 5–7 周（单 Agent 串行）或 4 周（双 Agent 并行 P2/P3）。

---

## 5. Phase 0 — 基线冻结与护栏

### 5.1 目标

确保后续改动可回滚、可测、不破坏九分闭环与 ENTRY v1。

### 5.2 任务清单

| # | 任务 | 细节 |
|---|------|------|
| 0.1 | 阅读基线 | `STATUS.md`、`ENTRY-DEFINITION-V1.md`、`NINE-POINT-CLOSED-LOOP-ACCEPTANCE`、本计划全文 |
| 0.2 | 验收模板 | 新建 `docs/ACCEPTANCE-TEMPLATE-CONSOLE.md`（复制下方阶段验收表头） |
| 0.3 | 路由拆分骨架 | 新建 `ab_screener/api/` 下 router 模块（空壳 + include），**先迁 1–2 个只读路由验证**（如 `/api/health` 保持兼容） |
| 0.4 | 测试基线命令 | 固化脚本或文档中的一键自检命令（见 §10） |
| 0.5 | 端口/健康探针 | 确认 `start_ui.ps1` 用 AB 专有 health 字段（`scanner_engine`/`build_version`），勿把 FinAgent 当 AB |

### 5.3 建议文件

```
ab_screener/api/
  __init__.py
  app_factory.py          # 可选：create_app()
  routers/
    health.py
    desk.py               # P1
    scan_profiles.py      # P1
    signals_lifecycle.py  # P2
    alerts.py             # P2
    constraints.py        # P3
    review.py             # P4
web/backend_app.py        # 逐步 include_router，最终变薄
```

### 5.4 验收标准（P0）

| ID | 标准 | 验证方法 |
|----|------|----------|
| P0-A1 | 现有 pytest 全绿（或与 STATUS 记载数量一致，新增不降） | `python -m pytest -q` |
| P0-A2 | ruff / 既有 lint 通过 | 项目约定命令 |
| P0-A3 | 前端 `tsc -b` 或 `npm run build` 通过 | `web/frontend` |
| P0-A4 | `GET /api/health` 仍返回 `status=ok` 且含 `build_version` 或 `scanner_engine` | curl/Invoke-RestMethod |
| P0-A5 | `GET /api/overview?pool=A` 200 | 同上 |
| P0-A6 | ENTRY v1 测试全绿 | `pytest tests/test_entry_definition.py` |
| P0-A7 | 无实盘开关被打开 | `LIVE_TRADING_ENABLED` 默认 false；测试断言 |

**P0 出口**：可开始 P1；产出 `docs/ACCEPTANCE-P0-YYYY-MM-DD.md` 一页。

---

## 6. Phase 1 — 指挥舱 + 扫描方案库 + 漏斗审计

### 6.1 目标

机构「盘前一屏 + 可复用扫描方案 + 漏斗可解释」。

### 6.2 领域模型

#### 6.2.1 `ScanProfile`（扫描方案）

```json
{
  "profile_id": "uuid",
  "name": "strict-default-v1",
  "description": "string",
  "version": 1,
  "entry_definition_id": "A_POOL_STRICT_NEXT_OPEN_V1",
  "params": {
    "top_n": 20,
    "days": 160,
    "workers": 0,
    "include_relaxed_in_a": false,
    "theme_soft_bonus": true,
    "box_overrides": null,
    "breakout_overrides": null
  },
  "universe": {
    "mode": "all_a_share",
    "exclude_st": true,
    "exclude_delisted": true,
    "min_list_days": 250,
    "codes": null
  },
  "created_at": "ISO",
  "updated_at": "ISO",
  "is_default": true
}
```

- 存储：SQLite 表 `scan_profiles`（migration 版本号按项目现有 migrations 体系递增）
- 禁止方案修改 `entry_timing`；`entry_definition_id` 只读展示

#### 6.2.2 `ScanRunFunnel`（漏斗快照）

每个扫描 run 落盘：

```json
{
  "run_id": "...",
  "profile_id": "...",
  "profile_version": 1,
  "config_hash": "c6824bb794e5",
  "code_version": "a85618f",
  "as_of": "YYYYMMDD",
  "stages": [
    {"name": "universe", "count": 5000},
    {"name": "prefilter", "count": 1200},
    {"name": "strict_breakout", "count": 40},
    {"name": "fund_filter", "count": 25},
    {"name": "fundamental", "count": 18},
    {"name": "a_pool", "count": 15},
    {"name": "b_pool", "count": 30},
    {"name": "defense_cleared_a", "count": 0}
  ],
  "regime": "defense|neutral|risk_on|...",
  "duration_ms": 123456
}
```

> **v1.1 数据血缘**：`config_hash`（config.py 内容 SHA）+ `code_version`（git sha）
> 随漏斗落盘，任何扫描结果都可回溯到「哪份代码 + 哪份参数」产出，与
> `release/readiness` 指纹体系同源。stage 计数必须从 `signals.py` 的
> `cond_break / cond_hold / cond_ma60` 等真实判定字段统计，禁止写死。

### 6.3 API（新增，REST JSON）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/desk/summary` | 指挥舱聚合：as_of、freshness、regime、open_allowed、latest_scan、alert_count、today_action |
| GET | `/api/desk/market-breadth` | **v1.1 新增**：市场宽度——涨跌家数、涨跌比、新高/新低、站上 MA20/MA60 比例、成交额中位数变化（daily 表聚合，见 §6.4.1） |
| GET | `/api/scan/profiles` | 列表 |
| POST | `/api/scan/profiles` | 创建 |
| GET | `/api/scan/profiles/{id}` | 详情 |
| PUT | `/api/scan/profiles/{id}` | 更新（version+1） |
| POST | `/api/scan/profiles/{id}/clone` | 克隆 |
| POST | `/api/scan` | **扩展**：body 可带 `profile_id`；缺省用 default profile |
| GET | `/api/scan/runs/{run_id}/funnel` | 漏斗 |
| GET | `/api/scan/runs` | 已有则扩展返回 `profile_id` |

`GET /api/desk/summary` 响应示例字段：

```json
{
  "as_of": "20260808",
  "freshness": {"label": "...", "is_stale": false},
  "regime": {"regime": "neutral", "label": "..."},
  "open_allowed": true,
  "block_reason": null,
  "latest_scan": {
    "run_id": "...",
    "status": "SUCCEEDED",
    "a_count": 12,
    "b_count": 20,
    "profile_name": "strict-default-v1",
    "finished_at": "..."
  },
  "today": { "primary_action": "...", "message": "..." },
  "alerts_unread": 0,
  "build_version": "...",
  "research_mode": "full"
}
```

### 6.4 后端实现要点

| 模块 | 职责 |
|------|------|
| `ab_screener/domain/scan_profile.py` | dataclass + 校验 |
| `ab_screener/data/scan_profile_repo.py` | SQLite CRUD |
| `ab_screener/application/scan_funnel.py` | 在现有扫描路径埋点计数 |
| `ab_screener/application/market_breadth.py` | **v1.1 新增**：市场宽度聚合（见 §6.4.1） |
| `run_screener.py` / `scan_job_runner.py` | 接受 profile 参数；写 funnel JSON 到 job 结果或独立表 |
| `market_regime.py` | 复用；desk 调用 |
| 已有 `/api/today` | desk 聚合复用，勿分叉逻辑 |

#### 6.4.1 市场宽度指标（v1.1 新增，机构指挥舱标配）

数据源：本地 `daily` 表（零新增外部依赖），按最新交易日聚合：

| 指标 | 定义 | 用途 |
|------|------|------|
| `advancers / decliners` | 当日上涨/下跌家数（close vs pre_close） | 多空力量 |
| `up_ratio` | 上涨家数占比（>0.5 偏多） | regime 辅助 |
| `new_highs / new_lows` | 近 60 日新高/新低家数 | 趋势确认 |
| `above_ma20_pct / above_ma60_pct` | 收盘站上 MA20/MA60 的占比 | 广度健康度 |
| `median_chg_pct` | 全市场涨跌幅中位数 | 与指数背离检测（指数涨、中位数跌=权重股行情） |
| `amount_yi` | 全市场成交额（亿） | 量能水位 |

实现注意：单次全市场聚合约 1–3 秒（516 万行 daily），**热缓存**（同 trade_date
结果缓存于内存，desk 轮询不重复算）；历史宽度序列落表 `market_breadth`
（trade_date 主键），供 regime 研究与 Review 页使用。指挥舱展示 6 宫格 + 近 20 日
`up_ratio` 迷你折线。

**漏斗埋点位置**（必须真实计数，禁止写死假数）：

1. universe 加载后  
2. prefilter 后  
3. strict 命中  
4. 资金过滤后  
5. 基本面后  
6. 入 A/B 池  
7. defense 清空 A 之后  

### 6.5 前端

| 页面 | 路由 | 内容 |
|------|------|------|
| 指挥舱 | `/` 增强 **或** `/desk` | 顶部状态、今日唯一动作、最新扫描摘要、漏斗迷你图、快捷入口 |
| 方案管理 | Overview 内抽屉 **或** `/screener/profiles` | CRUD 方案、设默认、用方案扫描 |
| 扫描历史 | 扩展现有 runs | 显示 profile 名 + 漏斗展开 |

**UI 文案强制**：

- 防守期：`open_allowed=false` 时主按钮禁用或二次确认「仅研究不可开仓」  
- Lab 入口标注「参数研究 · 非下单」

### 6.6 测试

| 测试文件 | 覆盖 |
|----------|------|
| `tests/test_scan_profiles.py` | CRUD、version 递增、default 唯一 |
| `tests/test_scan_funnel.py` | mock 扫描路径产生单调不增的 stage counts（universe ≥ … ≥ a_pool） |
| `tests/test_desk_summary.py` | 聚合字段齐全；stale 时 open_allowed 策略符合产品定义 |
| 前端 | 方案列表渲染；无 profile 时回退 default |

### 6.7 验收标准（P1）

| ID | 标准 | 验证 |
|----|------|------|
| P1-A1 | 可创建/列出/更新扫描方案，version 递增 | API + DB |
| P1-A2 | 默认方案存在；`POST /api/scan` 不带 profile 仍可用 | 回归 |
| P1-A3 | 带 `profile_id` 扫描成功后 `funnel` 可查，各 stage count 为 int 且逻辑单调 | API |
| P1-A4 | `GET /api/desk/summary` 含 as_of/regime/open_allowed/latest_scan | API |
| P1-A5 | 防守 regime 下 A 池策略与现网一致（清空或禁止新开） | 单测 + 手动 |
| P1-A6 | 方案不能改写 `entry_definition_id` 为非法值 | 校验 422 |
| P1-A7 | UI：指挥舱可见数据日期与开仓状态；用方案发起扫描 | 浏览器 |
| P1-A8 | 九分闭环相关 API（today/release/paper）不回归 | pytest 子集 |
| P1-A9 | ENTRY v1 仍被 scan 路径引用 | grep + 单测 |

**P1 出口文档**：`docs/ACCEPTANCE-P1-DESK-PROFILES-YYYY-MM-DD.md`

---

## 7. Phase 2 — 信号生命周期 + 预警中心

### 7.1 目标

候选股从「表行」变为「可跟踪对象」；关键事件可告警。

### 7.2 状态机

```
DETECTED → WATCHING → TRADEABLE → ENTERED → EXITED
                ↘ EXPIRED
                ↘ INVALIDATED（ST/停牌/跌破箱体/防守剔除）
```

| 状态 | 含义 | 进入条件（示例） |
|------|------|------------------|
| DETECTED | 扫描命中 | 扫描写入 |
| WATCHING | B 池或待观察 | tier=B 或人工标记 |
| TRADEABLE | A 池且 open_allowed | tier=A 且环境允许 |
| ENTERED | 纸面已成交开仓 | paper fill buy |
| EXITED | 已平仓 | paper fill sell / 模拟退出 |
| EXPIRED | 信号过期 | 突破日 + N 交易日未入场（默认 5，可配置） |
| INVALIDATED | 失效 | 跌破箱体/变 ST/数据错误 |

**转移必须写审计日志** `signal_events(signal_id, from, to, reason, at)`。

### 7.3 数据模型

表建议：

- `signal_instances`：`signal_id, ts_code, breakout_date, scan_run_id, profile_id, tier, state, entry_definition_id, payload_json, created_at, updated_at`
  - **v1.1 幂等键（强制）**：唯一约束 `(ts_code, breakout_date)`，重复扫描 UPSERT
    更新 tier/state，禁止重复建实例
- `signal_events`：事件流
- `alerts`：`alert_id, level, kind, ts_code, message, payload_json, read_at, created_at`
- `signal_outcomes`：**v1.1 新增（信号绩效前瞻回填）**——`signal_id, ts_code, breakout_date, entry_date, ret_5d, ret_10d, ret_20d, max_gain_10d, max_dd_10d, filled_at`
  - 由每日调度器（P5）在数据更新后回填：以 ENTRY v1 的 next-open 入场价计算
    5/10/20 日收益（若未入场则记 NULL）
  - Monitor 页展示「滚动胜率/平均收益/信号衰减曲线」= 个人版因子 IC/IR
- `audit_log`：**v1.1 新增（操作审计）**——`log_id, kind, actor, target_id, detail_json, created_at`
  - 记录：方案创建/修改、信号人工转移、纸面下单、约束变更、同步触发
  - 机构合规要求「谁在何时改了什么」，个人版实现为只读审计页（P5 `/system` 内嵌）

### 7.3.1 信号实例幂等与纸面衔接（v1.1 补强）

- 扫描写入用 `INSERT ... ON CONFLICT(ts_code, breakout_date) DO UPDATE`；
  同一突破日被多天扫描命中时只更新 tier/state/最新 run_id，不新增行
- 纸面 fill 匹配 ENTERED：按 `ts_code` 匹配最近一条 `TRADEABLE` 且
  `breakout_date <= fill_date` 的实例；无匹配只写 audit_log，不阻断

### 7.4 API

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/signals` | 过滤：state, tier, as_of, scan_run_id |
| GET | `/api/signals/{id}` | 详情 + 事件 |
| POST | `/api/signals/{id}/transition` | 人工转移（受限）；系统转移走内部 |
| GET | `/api/signals/outcomes` | **v1.1 新增**：信号绩效看板（滚动胜率/平均收益/衰减，按 tier/regime 切片） |
| GET | `/api/search?q=` | **v1.1 新增**：全局搜索——代码/名称/拼音首字母匹配，返回个股+方案+信号三类结果 |
| GET | `/api/alerts` | 列表 unread 优先 |
| POST | `/api/alerts/{id}/read` | 标记已读 |
| POST | `/api/alerts/read-all` | 全读 |
| GET | `/api/monitor/summary` | 各 state 计数 |

### 7.5 预警规则（最小集，P2 必须实现）

| kind | 触发 | level |
|------|------|-------|
| `data_stale` | freshness.is_stale | warning |
| `scan_failed` | job FAILED | error |
| `defense_on` | regime 进入防守 | info |
| `signal_invalidated` | 跌破箱体/ST | warning |
| `signal_expired` | 超期未入场 | info |
| `stop_triggered` | 纸面止损成交 | warning |
| `reconciliation_block` | 对账阻断（若已有） | error |

实现：`ab_screener/application/alert_engine.py`  
触发点：扫描结束、desk 拉取、日结/对账、可选定时 heartbeat。

### 7.6 前端 `/monitor`

- 表格：代码、名称、状态、突破日、箱体、所属 run、操作（查看/标记失效）
- 筛选：state tabs
- 右侧或顶：未读告警列表
- 点击 → 现有 `/stock/:code`

### 7.7 与纸面衔接

- 确认买单成功 → 对应 signal `ENTERED`（按 ts_code + 最近 TRADEABLE 匹配）
- 卖出成交 → `EXITED`
- 无 signal 时允许纸面（不阻断），但打 log

### 7.8 验收标准（P2）

| ID | 标准 | 验证 |
|----|------|------|
| P2-A1 | 扫描成功后 strict 命中写入 `signal_instances`，初始 state 正确 | DB |
| P2-A2 | 状态机非法转移返回 4xx | 单测 |
| P2-A3 | 事件表有完整 from→to 记录 | DB |
| P2-A4 | 至少 5 类预警可自动产生 | 单测 mock |
| P2-A5 | `GET /api/alerts` 未读计数与 desk.alerts_unread 一致 | API |
| P2-A6 | Monitor 页可按状态筛选并跳转个股 | 浏览器 |
| P2-A7 | 过期规则：可配置 `SIGNAL_EXPIRE_TRADING_DAYS`（默认 5） | 单测日历 |
| P2-A8 | 纸面开仓后 state→ENTERED（有匹配 signal 时） | 集成测 |
| P2-A9 | 不引入实盘；告警不含 Token | 审查 |
| P2-A10 | **v1.1** 同 ts_code 重复扫描不产生重复 signal 实例（幂等 upsert） | 单测 |
| P2-A11 | **v1.1** signal_outcomes 回填 ret_5/10/20 且与 ENTRY v1 next-open 口径一致 | 单测 |
| P2-A12 | **v1.1** 全局搜索命中代码/名称/拼音；audit_log 记录关键操作 | 单测 + 浏览器 |

**P2 出口**：`docs/ACCEPTANCE-P2-MONITOR-ALERTS-YYYY-MM-DD.md`

---

## 8. Phase 3 — 组合约束 + 暴露看板

### 8.1 目标

纸面下单前机构式风控；组合可解释暴露。

### 8.2 约束模型 `PortfolioConstraints`

```json
{
  "max_positions": 10,
  "max_single_weight": 0.15,
  "max_theme_weight": 0.40,
  "max_gross_exposure": 1.0,
  "min_cash_weight": 0.05,
  "deny_st": true,
  "deny_when_defense": true,
  "deny_when_data_stale": true
}
```

- 存：`runtime` 配置表或 `configs/portfolio_constraints.json` + DB 覆盖
- 默认值写入 `config.py` 常量

### 8.3 校验时机（强制）

在以下路径 **硬拦截**（HTTP 4xx + 结构化错误码）：

1. `POST /api/paper/orders/drafts`
2. `POST /api/paper/orders/{id}/confirm`
3. （可选）review 通过前

错误体：

```json
{
  "detail": {
    "code": "CONSTRAINT_VIOLATION",
    "message": "单票仓位将超过 15%",
    "details": {"constraint": "max_single_weight", "projected": 0.18, "limit": 0.15},
    "retryable": false
  }
}
```

### 8.4 暴露计算

`GET /api/portfolio/exposure`：

```json
{
  "as_of": "...",
  "nav": ...,
  "cash_weight": ...,
  "positions": [{"ts_code", "weight", "themes": []}],
  "by_theme": [{"theme", "weight"}],
  "by_tier_source": [{"source": "signal|manual", "weight"}],
  "top_concentration": {"top1": 0.12, "top3": 0.30, "top5": 0.45},
  "constraints": { ...current... },
  "breaches": []
}
```

主题映射复用 `sector_themes.py`。

#### 8.4.1 组合风险指标（v1.1 新增）

`GET /api/portfolio/risk`（数据来自纸面每日快照 equity_curve）：

| 指标 | 定义 |
|------|------|
| `sharpe` | 日收益年化 Sharpe（无风险利率取 0 或可配） |
| `sortino` | 下行波动率版 |
| `max_drawdown` | 区间最大回撤（已有，并入展示） |
| `var_95` | 历史模拟法日 VaR（95% 置信，滚动 60 日窗口） |
| `twr / mwr` | 时间加权/资金加权收益率（分别衡量策略与资金进出效率） |
| `vol_annualized` | 年化波动率 |

个人纸面组合的轻量风控仪表，全部基于既有快照数据计算，无新依赖。

#### 8.4.2 多股对比 `/compare`（v1.1 提级：原「延后」改为 P3 必做）

机构投研高频动作；数据全部复用 `/api/stock/{code}`，成本低。

- 入口：总览/监控多选（最多 6 只）→「加入对比」→ `/compare`
- 视图：并排指标表（价/涨幅/量比/箱体参数/资金流/基本面/评分）+ 归一化收盘走势
  叠加图（近 60 日，起点=100）+ 资金流对比柱
- 实现：`GET /api/compare?codes=a,b,c`（聚合后端返回）；前端新页面 + Sidebar 入口

### 8.5 前端

- Paper 页增加「风控」卡片：当前约束、是否触线、暴露条形图
- 草稿/确认失败时展示 `CONSTRAINT_VIOLATION` 人话

### 8.6 验收标准（P3）

| ID | 标准 | 验证 |
|----|------|------|
| P3-A1 | 默认约束加载成功 | API |
| P3-A2 | 超单票上限的 confirm/draft 被拒 | 单测 |
| P3-A3 | 超最大持仓数被拒 | 单测 |
| P3-A4 | 防守期 `deny_when_defense` 拒绝新开买入 | 单测 |
| P3-A5 | 数据 stale 且开关开启时拒绝买入 | 单测 |
| P3-A6 | exposure 权重和约 1.0（含现金，误差 <1e-4） | 单测 |
| P3-A7 | UI 展示 breaches | 浏览器 |
| P3-A8 | 卖出单不受 max_positions 限制 | 单测 |
| P3-A9 | 金额仍走整数分账本 | 既有 money 测试不回归 |
| P3-A10 | **v1.1** `/api/portfolio/risk` 指标数值与手算一致（Sharpe/DD/VaR 抽查） | 单测 |
| P3-A11 | **v1.1** `/compare` 支持 2–6 只并排 + 归一化走势叠加 | 浏览器 |

**P3 出口**：`docs/ACCEPTANCE-P3-CONSTRAINTS-YYYY-MM-DD.md`

---

## 9. Phase 4 — 复盘产品化

### 9.1 目标

将 CLI 归因/证据提升为控制台一等公民；周报可一键生成。

### 9.2 API

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/review/attribution/run` | 异步任务；参数 start/end/step/max_codes |
| GET | `/api/review/attribution/status` | 进度 |
| GET | `/api/review/attribution/latest` | 最新摘要 + 下载链接 |
| POST | `/api/review/evidence/run` | 包装 `build_evidence_report` |
| GET | `/api/review/evidence/latest` | 最新证据 |
| GET | `/api/review/weekly` | 聚合：本周信号、假突破率、纸面 PnL、regime 分布 |

复用：

- `ab_screener/research/attribution.py`
- `ab_screener/research/evidence.py`
- 任务持久化模式对齐 Lab `research_runs` 或新建 `review_jobs` 表

#### 9.2.1 研究假设登记 + 决策日志（v1.1 新增，机构研究纪律）

**idea ledger（假设登记）**：把回测工作台的「试验历史」推广为全站研究日志。
表 `research_notes`：`note_id, kind(hypothesis|experiment|conclusion), title, body_md, params_json, result_json, related_run_id, status(open|confirmed|rejected), created_at, updated_at`。

- 入口：Lab / Backtest / Review 页各一个「记一笔」按钮；跑完实验可一键把
  参数+结果存为一条 note
- Review 页展示按状态分组的假设列表（机构「研究台账」的个人版）

**决策日志（A 池人工决策）**：表 `decision_log`：`log_id, trade_date, ts_code,
decision(buy|skip|watch), reason_md, position_pct, created_at`。
- Monitor/总览 A 池每票提供「买入/跳过/继续观察」登记按钮
- P4 周报自动汇总本周决策 → 复盘闭环（选择质量可度量，而非事后拍脑袋）

### 9.3 前端 `/review`

区块：

1. **假突破归因**：标签分布饼图 + ret_5/10/20 表 + 运行按钮  
2. **证据包**：IS/OOS 净 PF、门禁结果、`can_claim_edge` 大红章（false 时灰色）  
3. **本周复盘**：自动聚合（无数据则空态）  
4. 下载 JSON/MD  

**强制文案**：页面顶栏「研究复盘 · 非下单指令」。

### 9.4 验收标准（P4）

| ID | 标准 | 验证 |
|----|------|------|
| P4-A1 | UI 可触发归因任务并看到完成结果 | 浏览器 + API |
| P4-A2 | 结果含 `entry_definition_id=A_POOL_STRICT_NEXT_OPEN_V1` | JSON |
| P4-A3 | 证据报告 `can_claim_edge` 在基线未过时为 false | 单测/实跑 |
| P4-A4 | 下载 md/json 文件存在且非空 | IO |
| P4-A5 | 与 CLI `run_attribution.py` 同参结果指标偏差可解释（允许任务 id 不同） | 抽样对比 |
| P4-A6 | Lab 页面仍标注非下单；Review 不提供「一键下单」 | UI 审查 |
| P4-A7 | 长任务可取消或至少可查询进度 | API |
| P4-A8 | **v1.1** research_notes 可登记/列表/关联 run；decision_log 可登记并进入周报聚合 | 单测 + 浏览器 |

**P4 出口**：`docs/ACCEPTANCE-P4-REVIEW-YYYY-MM-DD.md`

---

## 10. Phase 5 — 平台硬化与收尾

### 10.1 任务

| # | 任务 | 细节 |
|---|------|------|
| 5.1 | 路由拆分完成 | `backend_app.py` 仅装配；业务在 routers |
| 5.2 | 导出 | `GET /api/export/scan_run/{id}.csv` · `GET /api/export/signals.csv` |
| 5.3 | 系统页 `/system` | 数据深度、research_mode、build_version、端口说明、最近错误 |
| 5.4 | CI | GitHub Actions 或本地脚本：pytest + ruff + frontend build |
| 5.5 | 文档 | 更新 `STATUS.md`、`README.md` 导航、小白手册增加 Monitor/Review |
| 5.6 | 性能预算 | desk/summary < 500ms（热缓存）；overview 保持轻量列表 |
| 5.7 | 安全 | SPA 路径穿越回归；日志无 Token |
| 5.8 | **v1.1 统一每日调度器** | `ab_screener/application/daily_scheduler.py`：单个常驻线程按依赖编排「数据同步 → 市场宽度 → 信号 outcome 回填 → 失效/过期判定与告警 → 纸面日结」，每步幂等、失败重试、状态入 `scheduler_runs` 表；`/system` 页可见今日流水线状态。替代现有分散的纸面 60s 循环 |
| 5.9 | **v1.1 数据库备份** | 每日收盘后（或首次启动）将 `runtime/stock_data.db` 热备份至 `runtime/backups/db-YYYYMMDD.db`（sqlite3 backup API + WAL 检查点），保留最近 7 份；`/system` 显示最近备份时间 |

### 10.2 验收标准（P5）

| ID | 标准 | 验证 |
|----|------|------|
| P5-A1 | `backend_app.py` 行数显著下降（目标 <800 或较改造前减少 ≥40%） | wc |
| P5-A2 | 全量 pytest 绿 | CI/本地 |
| P5-A3 | 前端 build 绿 | npm |
| P5-A4 | CSV 导出可打开且 UTF-8 | 手工 |
| P5-A5 | STATUS.md 反映控制台阶段完成与已知限制 | 文档 |
| P5-A6 | `GET /api/release/readiness` 仍可用 | API |
| P5-A7 | 桌面启动：AB 8001 健康且 overview 200；不与 AETF Alpha(8000)/FinAgent(8010) 冲突 | 手工 |
| P5-A8 | **v1.1** 调度器完成一次完整日流程（同步→宽度→回填→告警→日结）且 scheduler_runs 有记录 | 实跑 |
| P5-A9 | **v1.1** 备份文件可恢复性抽查（备份→临时库→关键表行数一致） | 脚本 |

**P5 出口**：`docs/ACCEPTANCE-P5-PLATFORM-YYYY-MM-DD.md` + 更新 `docs/STATUS.md`

---

## 11. 跨阶段工程规范（所有 Agent 遵守）

### 11.1 代码放置

| 类型 | 路径 |
|------|------|
| 领域纯逻辑 | `ab_screener/domain/` |
| 应用服务 | `ab_screener/application/` |
| 持久化 | `ab_screener/data/` |
| HTTP | `ab_screener/api/routers/` |
| 前端页面 | `web/frontend/src/pages/` |
| 前端 API | `web/frontend/src/api/client.ts` |
| 迁移 | 沿用 `ab_screener/data/migrations_v2.py` 或 paper migrations 模式 |
| 验收文档 | `docs/ACCEPTANCE-P*-*.md` |

### 11.2 API 错误契约

与纸面一致：

```json
{"detail": {"code": "STRING", "message": "人话", "details": {}, "retryable": false}}
```

### 11.3 数据库

- 新表必须 migration；禁止手改生产 DB schema 不记版本  
- WAL；短连接  
- 大数据结果放 `runtime/` 或 out，不进 git  

### 11.4 测试要求

每阶段最少：

- 新增模块单元测试  
- 1 个 API 级测试（TestClient 或现有 paper API 模式）  
- 不降低既有 paper/lab/scan 关键测试  

### 11.5 提交与文档

- 每阶段结束：验收 MD + STATUS 短更新  
- 不提交 `.env`、Token、`runtime/*.db`  
- 用户未要求时不要 force-push  

### 11.6 自检（任务收尾强制）

每阶段结束 Agent 必须实际执行并在回复写 **自检** 小节：

```text
## 自检
- git: status 摘要
- pytest: N passed / failed
- lint: pass|fail
- typecheck/frontend: pass|fail|skipped
- 关键 API 冒烟: ...
结论: 本阶段可交付 / 不可交付
```

---

## 12. 数据流总图（实现对照）

```
                    ┌──────────── desk/summary ────────────┐
                    │ as_of · regime · open_allowed · alerts│
                    └───────────────┬──────────────────────┘
                                    │
ScanProfile ──► POST /api/scan ──► scan_jobs + funnel
                                    │
                                    ▼
                            signal_instances (P2)
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
         Monitor UI            Paper constraints      Review attribution
         + alerts              (P3) + exposure        (P4) + evidence
```

---

## 13. 优先级与裁剪指南

若工期不足，**保序裁剪**：

1. **不可裁**：P0、P1 方案+漏斗、P3 买入硬约束、ENTRY/门禁不破坏  
2. **可降级**：P2 人工 transition API（保留自动状态即可）  
3. **可延后**：P4 weekly 聚合、P5 CSV、对比页 `/compare`  
4. **不要做**：NL 选股、多用户、真券商  

---

## 14. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `backend_app.py` 过大难改 | P0 骨架 + 按域迁出；每迁一个路由配回归 |
| 扫描埋点漏计 | funnel 单测强制单调；无埋点则 stage 显式 null 而非假 0 |
| 信号与纸面匹配错误 | 仅匹配同 ts_code 最近 TRADEABLE；失败只 log |
| 归因任务过重 | 默认 max_codes 上限 300；异步 + 进度 |
| 端口被占 | 健康探针认 AB 字段；文档写明 8001/8000/8010 |
| Agent 误改 ENTRY | Code review checklist；测试锁 ID |
| **v1.1** 信号实例重复（全量扫描重写 vs 生命周期表） | 幂等键 `(ts_code, breakout_date)` upsert；P2-A10 锁定 |
| **v1.1** 市场宽度/outcome 回填计算过重拖慢启动 | 热缓存 + 统一调度器非阻塞线程；desk 预算 500ms |
| **v1.1** 约束引擎与既有纸面 risk 模型双轨 | 扩展现有 `paper_trading` 约束模型与拦截点，禁止新建平行风控 |

---

## 15. 给调度 Agent 的执行顺序（复制即用）

```text
1) 实现 P0 → 写 ACCEPTANCE-P0 → 自检全绿
2) 实现 P1 → ACCEPTANCE-P1 → 自检
3) 开两个子任务（若并行）：P2 Monitor | P3 Constraints
4) 合并后实现 P4 Review
5) P5 硬化 + 更新 STATUS.md + 最终总验收
```

**总验收清单（全部阶段完成后）**：

- [ ] 指挥舱一屏可读 as_of / 开仓 / 最新扫描 / 告警数  
- [ ] 扫描方案可保存并复用；漏斗可查  
- [ ] 信号有状态；监控页可用  
- [ ] 预警至少 5 类  
- [ ] 纸面买入受约束拦截  
- [ ] 暴露 API + UI  
- [ ] 复盘页可跑归因与证据  
- [ ] ENTRY v1 + 研究 fail-closed + 无实盘  
- [ ] 全量测试绿；STATUS 已更新  

---

## 16. 附录 A — 建议新增配置项（`config.py`）

```python
# Phase 2
SIGNAL_EXPIRE_TRADING_DAYS = 5
ALERT_RETENTION_DAYS = 30
SIGNAL_OUTCOME_HORIZONS = (5, 10, 20)   # v1.1 信号绩效回填窗口
AUDIT_LOG_RETENTION_DAYS = 180          # v1.1 操作审计保留期

# Phase 3
PORTFOLIO_MAX_POSITIONS = 10
PORTFOLIO_MAX_SINGLE_WEIGHT = 0.15
PORTFOLIO_MAX_THEME_WEIGHT = 0.40
PORTFOLIO_MIN_CASH_WEIGHT = 0.05
PORTFOLIO_DENY_WHEN_DEFENSE = True
PORTFOLIO_DENY_WHEN_DATA_STALE = True
RISK_VAR_WINDOW_DAYS = 60               # v1.1 历史 VaR 窗口
RISK_VAR_CONFIDENCE = 0.95
RISK_RISK_FREE_ANNUAL = 0.015           # v1.1 Sharpe 无风险利率（可配）

# Phase 4
REVIEW_DEFAULT_MAX_CODES = 250
REVIEW_DEFAULT_STEP = 10

# Phase 5
DB_BACKUP_KEEP_DAYS = 7                 # v1.1 备份保留份数
SCHEDULER_RUN_RETENTION_DAYS = 60
```

---

## 17. 附录 B — 前端路由最终态（目标）

| Path | 页面 | 阶段 |
|------|------|------|
| `/` 或 `/desk` | 指挥舱（含市场宽度）+ 扫描结果 | P1 |
| `/monitor` | 信号监控（含绩效回填看板） | P2 |
| `/stock/:tsCode` | 个股（增强 checklist） | P1–P2 可选增强 |
| `/compare` | 多股对比（v1.1 提级） | P3 |
| `/paper` | 纸面 + 约束/暴露/风险指标 | P3 |
| `/lab` | 策略实验室 | 已有 |
| `/backtest` | 回测工作室（含试验历史） | 已有 |
| `/review` | 复盘（含假设登记/决策日志） | P4 |
| `/system` | 系统（任务/数据/导出/审计/备份/版本） | P5 |

`Sidebar.tsx` / `Topbar.tsx` 必须同步。

---

## 18. 附录 C — 阶段验收文档模板

每阶段 `docs/ACCEPTANCE-P{n}-*.md` 必须包含：

```markdown
# P{n} 验收 — 标题 — 日期

## 环境
- commit:
- python:
- 数据 as_of:

## 验收表
| ID | 结果 PASS/FAIL | 证据 |

## 测试命令与输出摘要
## 已知限制
## 回滚说明
## 结论：可进入下一阶段 / 不可
```

---

## 19. 变更控制

| 变更类型 | 流程 |
|----------|------|
| 改 ENTRY 语义 | 升 v2 文档+代码+测试；旧报告标注 id |
| 改约束默认值 | 改 config + 单测 + 用户可见 changelog |
| 增预警 kind | 更新本计划附录与 alert_engine 枚举 |
| 跳过某阶段 | 在 STATUS 记录裁剪决定与影响 |

---

**计划结束。** 实现 Agent 从 **Phase 0** 开始，完成即写验收文档，再进入下一阶段。
