# 当前状态与收口路线（2026-08-31）

> 本文件由外部检查生成，用于记录 **2026-08-31 当天的真实仓库与运行态**。
> 不覆盖 `docs/STATUS.md`（该文件按约定由用户维护）。
> 工程状态 ≠ 研究结论。本文件不构成任何 edge、可跟单或收益承诺。

## 0. 一句话结论

系统**已经在跑、也已经可用**：8001 后端正在服务、行情新鲜、扫描正常。
真正的问题不是"坏了"，而是**同一个项目分裂成两份代码副本**——权威文档指向主副本，
实际开发和运行却在集成工作树里，且这批工作从未合回 `main`。收口 = 把这条岔路合上。

## 1. 真实拓扑（关键，容易搞错）

| 位置 | 分支 | 最后提交 | 角色 |
|---|---|---|---|
| `E:\CODEX\Stock_selection\accumulation_breakout` | `closers-g2-split` | 2026-08-23 | 文档宣称的权威副本，**代码已落后** |
| `E:\CODEX\Stock_selection\worktrees\v2r-final-integration` | `v2r-final-integration` | **2026-08-31 08:44** | 实际开发 + 实际运行的副本 |
| `origin/main`（GitHub） | `main` | `2c04962` / 2026-08-21 | **落后所有本地工作** |

- 共 **15 个本地分支 / 13 个 git 工作树**（`v2r-a/d/f/n/o1/o2/q1/r/s/x`、两个 wave 集成分支、`regime-parity`）。
- 8001 后端进程由 `runtime/start_backend_authoritative.ps1` 拉起：**代码取工作树，数据取主副本的生产库**
  （`AB_DB_PATH=...\accumulation_breakout\runtime\stock_data.db`，约 16.5 GB）。
- 风险：双击主副本的 `一键启动.bat` 会启动 **08-23 的旧代码**，而不是每天在用的那份。

## 2. 运行态快照

- 行情基准日 `as_of = 20260828`，新鲜度「滞后 0 个交易日」。
- 最近一次扫描：市场环境 **防守**（沪深300 收于 MA20 下方）→ `allow_new_entries=false`，
  **A 池 = 0、B 池 = 30**。这是风控设计的预期行为，不是故障。
- 当前生效参数 `profile_id=manual-daily-research-scan`，`source_kind=MANUAL_RESEARCH`，
  自带标注：**未经 IS/OOS、WF、基线与成本压力验证**。
- 8-24 起工作树新增的产品能力：精简版日用面（每日选股 / 个股详情 / 专业回测）、
  专业回测网格与 AI 证据评测、全局长任务进度、应用内引导与分类维度、供给枯竭机制预登记。

## 3. 龙虎榜 T01–T12 剩余量

工程实现 **T01–T12 全部已交付并有 handoff**；卡点集中在第 6 节的"正式通过"条件。

| 任务 | 工程 | 唯一未打勾的验收项 |
|---|---|---|
| T01 契约与迁移 | 完成 | — |
| T02 抓取与原始快照 | 完成 | — |
| T03 回填与跨源对账 | 完成 | 对账代码就绪，**真实官方数据未授权** |
| T04 事件标准化去重 | 完成 | — |
| T05 席位主数据与身份图谱 | 完成 | — |
| T06 行为特征与协同网络 | 完成 | — |
| T07 画像 | 完成 | — |
| T08 信号与硬否决 | 完成 | — |
| T09 事件研究与反过拟合 | 完成 | — |
| T10 API 与仪表盘 | 完成 | — |
| T11 盘后 DAG 与告警 | 完成 | 告警全部 `CREATED + dry_run=1`，**未送达未 ACK** |
| T12 Shadow 与发布门禁 | 完成 | **shadow maturity 未达标**（需 ≥3 个月且 ≥30 个成熟独立信号） |

第 4 节「边界情况验收清单」20 项**整段未勾选**（清单里从未逐项签收）。

### 第 6 节三道未过的硬门，按可关闭性分类

| 硬门 | 能否快速关闭 | 原因 |
|---|---|---|
| 全仓 Ruff 114 条存量债 | **能** | 纯代码整改，与研究无关 |
| 官方跨源核验 `NOT_AUTHORIZED` | **不能** | 交易所数据授权问题，写代码解决不了 |
| Shadow maturity | **不能** | 按定义需要 3–12 个月真实积累 |

结论：LHB 的"正式工程通过"在拿到官方数据授权之前**不可能**宣告。当前 `READY（仅隔离副本、盘后研究）`
已经是这批工作能达到的最高诚实状态，继续往前推的性价比很低。

## 4. 收口路线（按此顺序）

### 第 1 刀 —— 先体检，不动手
```
双击 收口诊断.bat
```
只读。打印工作树拓扑、各分支与 `origin/main` 的落差、**合并冲突预演**、端口占用、
后端身份、三个数据库体积、硬门状态与最近扫描摘要，并落地一份报告到 `runtime\`。

### 第 2 刀 —— 合并集成分支（看完诊断再做）
诊断报告的第 3 节说无冲突就直接走：
```powershell
cd E:\CODEX\Stock_selection\accumulation_breakout
git fetch origin
git switch -c 收口-20260831 origin/main
git merge --no-ff v2r-final-integration
# 冲突则逐个解，解完再 commit
git push -u origin 收口-20260831        # 或直接推 main
```
合并后主副本切到该分支，**主副本与工作树的代码才真正一致**。
`closers-g2-split` 的 12 个提交需单独确认是否已被 `v2r-final-integration` 包含
（诊断报告第 2 节的落差数字会告诉你）。

### 第 3 刀 —— 日常只用一个入口
```
双击 每日运行.bat
```
同步行情 → 拉起 8001 → 发起扫描 → 打印 A/B 池与市场环境。
硬门在脚本里强制为 `false`，且拒绝指向 `lhb_product.db`。
合并完成后，把 `daily_run.ps1` 里的 `-Root` 默认值改回主副本路径，工作树即可 `git worktree remove`。

### 第 4 刀 —— 清理（可选）
- 12 个已完成的 `v2r-*` 工作树：`git worktree remove` + `git branch -d`。
- `runtime\lhb_product.db` 16.5 GB：若近期不推进龙虎榜，删除可释放约 16 GB。
- 主副本 `runtime\` 下 8-06～8-11 的一次性探针脚本（`_probe*.py`、`_lab*.json` 等）。

## 5. 明确不做的事

- 不打开 `LIVE_TRADING_ENABLED`、`DAILY_SCHEDULER_ENABLED`、`V2_PIT_READ_ENABLED`。
- 不把 A 池、B 池或龙虎榜信号描述为荐股、可跟单或已验证有效。
- 不覆盖 `docs/STATUS.md`、不改 `configs/platform_v2.yaml`、不动生产库。
- 不把 `MANUAL_RESEARCH` 参数描述为通过验证的参数。
