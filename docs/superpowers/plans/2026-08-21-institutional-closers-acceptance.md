# 个人机构化收口 — 验收矩阵（2026-08-21）

> 对应计划 `PERSONAL-INSTITUTIONAL-CLOSERS-2026-08-21`。  
> 检查 Agent 必须 **亲自复跑** 命令，不得沿用实现方口头结果。  
> 总评禁止出现 `PERSONAL_INSTITUTIONAL_READY`（除非七闸门机器证据全 PASS **且** 用户已合并 STATUS — 本轮预期不会发生）。

## 通用否决（一票拒绝该波次）

- 打开 `LIVE_TRADING_ENABLED`
- 改 V1 入场定义或让 V2 条件静默冒充 V1
- astock / 情报写入 A 池、扫描、纸面订单
- `INSERT OR REPLACE` 进入生产路径
- 用 Python 3.14 或损坏的 `.venv312` 启动器作为唯一 pytest 证据
- 覆盖 `docs/STATUS.md` / `docs/RESEARCH-ROADMAP.md`
- 把研究 FAIL 改写成 PASS
- 在 Wave F 之前修改 `configs/platform_v2.yaml` 把生产旗标改为 true
- PIT `--run` 直接打生产库且无备份/无用户维护窗口记录

## 权威命令环境

见 [手册](2026-08-21-institutional-closers-agent-runbook.md)。下文 `$Py` = 3.12 证据解释器。

```powershell
cd E:\CODEX\Stock_selection\accumulation_breakout
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null
$env:PYTHONPATH = "E:\CODEX\Stock_selection\accumulation_breakout;E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Lib\site-packages"
```

## W0 环境

| ID | 项 | 命令 / 检查 | 通过 |
|---|---|---|---|
| W0-1 | 仓库 | `git rev-parse --short HEAD` 与 `origin/main` | 能解释相对 `2c04962` 的关系 |
| W0-2 | 架构 | `$Py scripts\check_architecture.py --strict` | exit 0 |
| W0-3 | 旗标 | 打印 `load_resolved_config()["flags"]` | LIVE=false；除 dual-run 外生产项 false |
| W0-4 | 库可读 | SQLite `mode=ro` 打开 `runtime/stock_data.db` | 不报错；记下 daily MAX |

## D 数据

| ID | 项 | 命令 / 检查 | 通过 |
|---|---|---|---|
| D1 | 日线新鲜 | `sync_daily.py` 后 `SELECT MAX(trade_date) FROM daily` | 等于源端最近已收盘交易日 |
| D2 | 副本回填 | `backfill_pit_v2.py --db <绝对副本> --preflight` 再 `--run` | preflight PASS；run 可断点续跑 |
| D3 | stock_basic | `SELECT COUNT(*) FROM stock_basic_history` | >0 或书面 INSUFFICIENT |
| D4 | cyq | checkpoints done vs 交易日分区 | 补完或书面剩余 |
| D5 | fina / holder | COUNT + coverage | 完成 **或** INSUFFICIENT（权限/时间），禁止假行 |
| D6 | coverage 落盘 | `--coverage` → `runtime/v2/pit_coverage_<stamp>.json` | 文件存在且含 partitions/done/rows |
| D7 | 未开 PIT 读 | `configs/platform_v2.yaml` | `V2_PIT_READ_ENABLED: false` 除非已进入 Wave F 且 F1 全绿 |
| D8 | 只读边界 | 回填脚本未改 `signals.py` 入场；未写 paper 表 | grep / 测试 |

D 闸门仍标 INSUFFICIENT，直到：D1 过 + 声明数据集 coverage 可复算 + 抽样 hash +（F1 才开读）。  
**代码任务**可在 D1–D6 过时标 `READY_FOR_REVIEW`。

## O 运维

| ID | 项 | 命令 / 检查 | 通过 |
|---|---|---|---|
| O0 | 备份根 | 环境变量 `AB_BACKUP_ROOT` | 绝对路径、可写、**不在** `runtime\` 下 |
| O1 | 一份备份 | `create_backup` | 文件存在；hash 校验；失败未更新 last good |
| O2 | 恢复演练 | `scripts\restore_backup.ps1` | DryRun 先过；实跑 RTO ≤1800s；不覆盖生产 |
| O3 | 七份策略 | `backup_ok` / 目录列表 | keep=7 且未删唯一备份。跨日更严，同日多次须在 handoff 声明 |
| O4 | soak | `soak_monitor_v2.py --collect` | ≥5 个不同交易日 manifest COMPLETE 才算 O-12；否则 INSUFFICIENT |
| O5 | 不把纸面当 soak | `pt_cycle` 行数 | 不得单独作为 O4 证据 |
| O6 | 调度仍关 | yaml | `DAILY_SCHEDULER_ENABLED: false` 除非 F3 |

O-min 交付：O0+O1+O2。  
O 闸门 PASS：O-min+O3+O4。本轮允许只交 O-min。

## E 工程债

### E2 / G2

| ID | 项 | 通过 |
|---|---|---|
| G2-1 | 宿主只装配 | `backend_app.py` 无业务路由函数；只 include_router / 中间件 / SPA |
| G2-2 | 无重复 path | `tests/test_openapi_contract_v2.py` 绿；OpenAPI path 唯一 |
| G2-3 | HTTP 契约不变 | 旧 `/api/*` 仍在；v2 `/api/v2/*` 仍在 |
| G2-4 | 离线测试 | `$Py -m pytest tests\ -q -k "not browser"` exit 0 |
| G2-5 | 架构 | `--strict` exit 0 |
| G2-6 | 行为 | 扫描仍子进程+进度；取消/失败落库语义不变 |

逐步验收：方案中的 5 步每步都要 G2-4。失败立刻停并回退该步。

### E3 / G3

| ID | 通过 |
|---|---|
| G3-1 | `ab_screener` 内有 signals/local_store/run_screener 正式入口 |
| G3-2 | 根文件为薄 re-export，导入旧路径仍可用 |
| G3-3 | 离线 pytest 绿 |

### E4 / G4

| ID | 通过 |
|---|---|
| G4-1 | 有前后 bench 数字（同一机器、同一输入） |
| G4-2 | 无新增 N+1 热循环（评审 + 抽样 profile） |
| G4-3 | 正确性测试不回退 |

### E5 / G5

| ID | 命令 | 通过 |
|---|---|---|
| G5-1 | CI architecture 步为 `--strict` | yaml 已提交且与本地一致 |
| G5-2 | `scripts\quality_gate.ps1 -Strict` | exit 0（前端改动不可 `-SkipFrontend`） |

### E6 / G6

| ID | 通过 |
|---|---|
| G6-1 | `docs/INDEX.md` 入库并指向当前契约与本套 closers |
| G6-2 | 不覆盖 STATUS / RESEARCH-ROADMAP |

## R 研究

| ID | 项 | 通过 |
|---|---|---|
| R1 | 结论诚实 | verdict 为 PASS/FAIL/INSUFFICIENT_EVIDENCE 之一，与 checks 一致 |
| R2 | 无候选泄漏 | FAIL/INSUFFICIENT 时 `candidate_eligible=false` 且 candidates 表不加新晋级行 |
| R3 | 不改 V1 | `tests/test_entry_definition_v1_golden.py` 绿 |
| R4 | 不进纸面 | 研究代码路径无 `pt_order` insert |

R 波次 **FAIL 也算该波交付成功**（证据新鲜）。R 闸门本身仍非 PASS。

## F 旗标（默认本轮不做）

每开一个旗标，必须附：前置 ID 清单 + 关闭回滚方式（改回 yaml false，不删 v2 表）。

| ID | 旗标 | 最少前置 |
|---|---|---|
| F1 | PIT 读 | D1 D6 + 抽样 hash + 双读无漂移测试 |
| F2 | v2 控制台 | 空数据 INSUFFICIENT；情报 disclaimer；不进 A 池测试仍绿 |
| F3 | 日调度 | O-min；失败步骤不绕过门禁 |
| F4 | 执行写 | dual-run parity 报告 |
| F5 | 风控 enforce | 稳定拒绝码测试绿；卖出不被误拦 |
| F6 | 策略 registry | 未验证插件仅实验标记 |

F0：`LIVE_TRADING_ENABLED` 保持 false。启动加载 true 必须抛错（已有测试）。

## G 总验收

| ID | 项 | 通过 |
|---|---|---|
| G-1 | 独立复跑 | 检查 Agent 重跑本矩阵相关命令 |
| G-2 | 身份 | worktree 相对声称提交干净；或明确列出脏文件 |
| G-3 | 总评用词 | 只允许 `ACCEPTED_ENGINEERING_SLICE` / `BLOCKED` / `REJECTED` |
| G-4 | 七闸门 | 用 `evaluate_readiness` 口径描述；缺证据 = INSUFFICIENT 不是 PASS |
| G-5 | 禁止 | 文档中不得出现 Agent 自宣布的 `PERSONAL_INSTITUTIONAL_READY` |

若 D+O 过且 R 仍 FAIL，检查报告可写：

```text
suggested_status: ENGINEERING_READY_RESEARCH_BLOCKED
claimed_by_agent: no
requires_user_STATUS_merge: yes
```

## 回归包（任一代码波次结束必跑）

```powershell
& $Py scripts\check_architecture.py --strict
& $Py -m ruff check web\backend_app.py ab_screener scripts tests --exclude web\frontend\node_modules
& $Py -m pytest tests\ -q -k "not browser"
```

前端有改动：

```powershell
cd web\frontend
node .\node_modules\typescript\bin\tsc --noEmit
# 或 npm run build（质量门）
```

PowerShell 可能拦截 `npx`；用 `node .\node_modules\typescript\bin\tsc --noEmit`。
