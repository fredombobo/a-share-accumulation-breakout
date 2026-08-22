# 个人机构化收口实施计划（2026-08-21）

> 契约版本：`PERSONAL-INSTITUTIONAL-CLOSERS-2026-08-21`  
> 状态：冻结，供其它 Agent 实现。  
> 前置：先读 [审计](2026-08-21-institutional-closers-audit.md) 与 [验收](2026-08-21-institutional-closers-acceptance.md)。  
> 总状态目标：**先** `ENGINEERING_READY_RESEARCH_BLOCKED`；**禁止**本计划结束时宣布 `PERSONAL_INSTITUTIONAL_READY`。

## 0. 硬约束（写进每个 Agent 提示词）

1. `LIVE_TRADING_ENABLED=false`。任何环境尝试 true 必须启动失败。禁止券商适配器。
2. 不改冻结入场 `A_POOL_STRICT_NEXT_OPEN_V1`。新条件只能走 V2 ID。
3. 研究 FAIL ≠ 可以进 A 池。Lab / trusted report 不是订单。
4. astock 桥保持只读。禁止 PE 选股、涨停梯队、指数快照写入扫描或纸面。
5. 禁止 `INSERT OR REPLACE`。upsert 用 `ON CONFLICT DO UPDATE`。
6. Tushare 只经 `tushare_init.py`。禁止裸 requests。禁止全市场 `fina_indicator` 无分区死循环。
7. 不覆盖 `docs/STATUS.md`、`docs/RESEARCH-ROADMAP.md`。
8. PIT 回填只打 **绝对路径副本**，禁止直接打生产 `runtime/stock_data.db`，除非用户书面维护窗口 + 已验证备份。
9. 生产旗标默认保持 false。Wave F 之前禁止改 `configs/platform_v2.yaml`。
10. 证据 Python 必须是 3.12（见手册）。禁止用 3.14 / 裸 `python` 当唯一证据。
11. Agent 不得自称 ACCEPTED / READY。只能 `READY_FOR_REVIEW`。

## 1. 波次总览

```text
Wave 0  读文档 / 环境核验          （所有 Agent 先做）
Wave D  数据新鲜度 + PIT 收口      （要 Token + 维护窗口；可与 E 并行）
Wave O  备份 / 恢复 / soak         （要用户给 AB_BACKUP_ROOT；跨交易日）
Wave E  工程债 G2–G6               （纯代码；不碰旗标、不碰账本）
Wave R  研究复跑（可选、fail-closed）（不得晋级）
Wave F  旗标切换                   （仅检查 Agent + 用户；实现 Agent 禁止自行开）
Wave G  诚实验收                   （独立检查；不得自签 READY）
```

**并行规则**

- Wave E 可与 D/O 同时进行。
- Wave R 可在 D 新鲜度恢复后跑，但结论 FAIL 时必须保持 FAIL。
- Wave F 必须 D 验收 + O 最低备份验收之后。
- Wave G 必须 F 之后或明确「旗标仍关、状态仍 BLOCKED」的诚实声明。

**不要做的大范围**

- 不要做 OMS / 实盘 / 多租户 / L2 / 新闻舆情伪造。
- 不要为了「更机构」加页面数量。
- 不要把 Logic Platform Phase 2/3 接进纸面，除非过研究闸门（G7）。

## 2. Wave 0 — 环境与基线（0.5h，所有人）

**做**

- 确认仓库：`E:\CODEX\Stock_selection\accumulation_breakout`
- 确认 `origin/main`（审计时 `2c04962`）
- 跑通手册里的 Python / git 路径
- 复跑 `scripts/check_architecture.py --strict`（期望 0）
- 只读打印旗标与 `daily` MAX

**不做**：改代码、开旗标、扫全市场、提交用户文档。

**验收**：见验收矩阵 W0。

## 3. Wave D — 数据闸门收口

目标：让 D 从「核心已回填、读路径未开」变成「覆盖可复算 + 日线不陈旧」。  
**本波不打开** `V2_PIT_READ_ENABLED`。

### D1 日线新鲜度（先做，短）

```powershell
# 见手册 Python 绑定
& $Py sync_daily.py
```

验收：`SELECT MAX(trade_date) FROM daily` 等于源端最近 **已收盘** 交易日（16:00 前允许仍是上一交易日）。  
扫描结果陈旧不阻塞 D1，但应在 D1 后由用户/后续任务决定是否重扫；**不要**自动把旧 A 池当今日可交易。

### D2 PIT 空表 / 未完表（维护窗口）

只对副本：

```text
副本建议：E:\ab-maintenance\stock_data_copy.db
先：scripts/migrate_v2.py --db <副本> --plan
    scripts/backfill_pit_v2.py --db <副本> --preflight
```

数据集优先级：

| 优先级 | 数据集 | 审计现状 | 备注 |
|---|---|---|---|
| P0 | （不要重跑）daily / daily_basic / adj_factor / moneyflow | 已 976/976 | 除非 coverage 显示缺口 |
| P1 | `stock_basic` | 0 行 | ALL 单分区，快 |
| P1 | `cyq` | 548/976 | 断点续跑 |
| P2 | `holder` | 0 | 按 ts_code；慢 |
| P2 | `fina_indicator` | 0 | **必须按 ts_code 分区**；禁止无分区全市场循环 |
| P3 | `top_list` 补洞 | 967 | 缺日补齐 |
| skip | 公告/新闻/一致预期/同花顺概念 | 权限不足 | 写 INSUFFICIENT，禁止假数据 |

命令形态：

```powershell
& $Py scripts\backfill_pit_v2.py --db <绝对路径副本.db> --run --datasets stock_basic
& $Py scripts\backfill_pit_v2.py --db <绝对路径副本.db> --run --start 20220819 --end <源端最新> --datasets cyq --workers 4
& $Py scripts\backfill_pit_v2.py --db <绝对路径副本.db> --coverage
```

磁盘：库已 ~12.3 GB。preflight 要求可用空间 ≥ 2×DB + 预计新增。空间不够 = 停，不要硬跑。

副本验证通过后，**经用户确认**再替换/同步生产库。Agent 不得悄悄覆盖。

### D3 覆盖与抽样

- `--coverage` 对已声明数据集给出 partitions/done/rows
- 已齐表：抽样 content_hash（可复用 data_quality / 既有 20×5 源端比对）
- 权限不足的表：在 handoff 写死 `INSUFFICIENT` 原因，不填 0 冒充覆盖

### D4 公司行为账本

`corporate_actions` 审计时 0 行。本波只做：

- 确认迁移已应用（已是）
- 若有 Tushare 公司行为接口：按既有 `corporate_action_repository` **追加**写入，禁止 REPLACE
- 无权限：记 INSUFFICIENT，不阻断 D1/D2 的代码任务，但 **D 闸门仍不能标 PASS**

### D 完成定义（仍不等于开 PIT 读）

- 日线不陈旧（D1）
- `stock_basic_history` 非 0，或书面 INSUFFICIENT
- `cyq` 分区补完或书面剩余原因
- `fina_indicator` / `holder`：完成 **或** 诚实 INSUFFICIENT（权限/耗时/用户停）
- coverage 报告落盘到 `runtime/v2/pit_coverage_<stamp>.json`
- **不改** `platform_v2.yaml`

## 4. Wave O — 运维闸门收口

目标：备份可恢复、soak 可计数。跨多个交易日，**单次会话无法关完 O-12**。

### O0 用户前置（Agent 不能编造）

用户必须提供：

```text
AB_BACKUP_ROOT = 不在 runtime\ 内的第二路径（建议独立盘）
```

未提供 → O 保持 INSUFFICIENT，Agent 只写 handoff「等待用户路径」，不要把备份写进项目目录冒充。

空间：7 份 × ~12 GB ≈ 84 GB+。先算再拷。

### O1 备份

按 `docs/BACKUP-RESTORE-RUNBOOK-V2.md`：

- `create_backup(db, backup_root)`
- hash 校验失败丢弃临时文件
- `prune_old_backups(keep=7)` 且不删除唯一备份

单日可先产出 **1 份已校验备份**（O1 最低）。满 7 份需要连续日或用户允许的多次快照（handoff 写清是「7 份同日快照」还是「7 个交易日」——验收认交易日更严，见验收矩阵）。

### O2 恢复演练

```powershell
powershell -ExecutionPolicy Bypass -File scripts\restore_backup.ps1 `
  -BackupRoot <AB_BACKUP_ROOT> `
  -RestoreTo <绝对路径演练目标.db> `
  -DryRun
# 用户确认后去掉 DryRun，计时必须 ≤ 1800s
```

演练目标不得直接覆盖生产库。先 `.pre-restore`。

### O3 Soak（跨日）

```powershell
& $Py scripts\soak_monitor_v2.py --db runtime\stock_data.db --soak-dir runtime\v2\soak --collect <YYYYMMDD>
```

不足 5 个不同 **COMPLETE** 交易日 → O-12 固定 INSUFFICIENT。禁止把纸面 `pt_cycle=10` 当成 soak。

DAG：`DAILY_SCHEDULER_ENABLED` 在 Wave F 之前保持 false。本波可用手工日结/手工备份产生 soak 证据。

### O 完成定义

最低可交付（O-min）：`AB_BACKUP_ROOT` 可写 + 1 份校验备份 + 一次计时恢复（DryRun 或实跑记录）。  
O 闸门 PASS：O-min + ≥7 份策略满足 runbook + soak ≥5 COMPLETE 日。  
本计划允许 Agent 只交付 O-min，把 5 日 soak 标成「进行中」。

## 5. Wave E — 工程债（可并行）

**一律不改生产旗标、不改入场定义、不改研究结论。**

### E2 = G2 拆路由（优先）

权威方案：[2026-08-21-g2-backend-split.md](2026-08-21-g2-backend-split.md)

五步，每步全量离线 pytest。宿主最终只装配。OpenAPI 无重复 path。  
现状：`web/backend_app.py` ≈ 2648 行。

### E3 = G3 根脚本迁包

`signals` / `local_store` / `run_screener` 等进入 `ab_screener` 正式入口；根文件变薄 re-export。行为不变。

### E4 = G4 性能

回测按 code 切片复用；扫描/证据路径去掉 N+1 热循环。要有 bench 前后数字，禁止无基线「感觉快了」。

### E5 = G5 CI

工作区已有 `ci.yml` architecture `--strict` 脏改动。本波：确认 `--strict` 在 CI 绿，然后提交。不要只改 yaml 却让 CI 红。

### E6 = G6 文档

`docs/INDEX.md` 指向当前契约；历史验收降为 archive。已有未跟踪草稿，合并本套 closers 链接后一起提交。

### E7 = G7（只守门，不扩功能）

Logic Platform Phase 2/3 保持 research_only。发现进纸面的路径 = 缺陷，修回 fail-closed。

## 6. Wave R — 研究（可选，fail-closed）

**目的**：在数据新鲜后复跑可信实验，更新 FAIL/INSUFFICIENT 证据。  
**不是**：找出能买的参数并写进 A 池。

允许：

- `scripts/run_trusted_research_real.py` 或既有 Lab 可信路径
- 结论原样落盘
- 若仍 FAIL：handoff 写 FAIL 原因，候选仍为 0

禁止：

- 改阈值让报告变绿
- 把 V1 入场偷偷改成 V2 条件
- `candidate_eligible=true` 在闸门未过时
- 研究 PASS 自动转订单（即便真 PASS 也要人工晋级，本波不做晋级）

READY 被 R 挡住是 **正确** 的。本波成功标准是「证据新鲜且诚实」，不是「策略赚钱」。

## 7. Wave F — 旗标（检查 Agent + 用户）

实现 Agent **不要做这一波**，除非用户点名且验收矩阵对应项全绿。

建议顺序（每步单独 commit / 单独验收）：

| 顺序 | 旗标 | 前置 |
|---|---|---|
| F1 | `V2_PIT_READ_ENABLED` | D1 + 核心 PIT coverage + 抽样 hash；双读对比无漂移 |
| F2 | `INSTITUTIONAL_CONSOLE_V2_ENABLED` | v2 页面不把空表当 PASS；情报 disclaimer 仍在 |
| F3 | `DAILY_SCHEDULER_ENABLED` | O-min 已过；调度失败不能下单（本来也无 LIVE） |
| F4 | `V2_EXECUTION_WRITE_ENABLED` | dual-run parity 证据 |
| F5 | `V2_RISK_ENFORCEMENT_ENABLED` | 先 observe 再 enforce；卖出不被买入集中度误拦 |
| F6 | `V2_STRATEGY_REGISTRY_ENABLED` | 六形态仅实验标记；未过研究门禁不得进 A |

永远 false：`LIVE_TRADING_ENABLED`。

## 8. Wave G — 诚实验收

独立检查 Agent：

- 重跑验收矩阵命令
- 更新 `docs/ACCEPTANCE-V2-P8-EVIDENCE-*.md` 或新写 `docs/ACCEPTANCE-CLOSERS-YYYY-MM-DD.md`
- 总评只能：`ACCEPTED_ENGINEERING_SLICE` / `BLOCKED` / `REJECTED`
- **禁止**输出 `PERSONAL_INSTITUTIONAL_READY`
- 若 D+O 仍缺：总评 BLOCKED，列出缺项
- 若 D+O 过且 R 仍 FAIL：可以建议状态机显示 `ENGINEERING_READY_RESEARCH_BLOCKED`，但仍须身份一致（worktree clean、baseline 重采）。`STATUS.md` 由用户合并，Agent 不改

## 9. 建议的 Agent 分工

| Agent | 波次 | 可并行 | 预计 |
|---|---|---|---|
| Env | W0 | — | 0.5h |
| Data | D1–D3 | 与 E 并行 | 数小时–数日（fina/holder） |
| Ops | O0–O2 | 与 E 并行；O3 跨日 | O-min 数小时；soak 5 个交易日 |
| Eng-G2 | E2 | 是 | 按拆分方案 5 步 |
| Eng-rest | E3–E6 | G3 依赖 G2 | 1–3 日 |
| Research | R | D1 之后 | 视网格；可能 FAIL |
| Gate | F + G | 最后 | 检查，不开发 |

同一工作树禁止两个 Agent 同时改 `web/backend_app.py` 与 PIT 生产库。

## 10. 完成时仓库应有的新文件

每个实现 Agent 必须写：

```text
docs/handoffs/CLOSERS-<WAVE>-<AGENT>.md
```

检查 Agent 必须写：

```text
docs/ACCEPTANCE-CLOSERS-<YYYY-MM-DD>.md
```

模板见手册。
