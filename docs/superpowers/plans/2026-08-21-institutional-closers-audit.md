# 个人机构化距离审计（冻结 2026-08-21）

> 审计方：Grok（只读评估，非实现 Agent）。  
> 代码基线：GitHub `origin/main` `2c04962`（PR #1 astock 桥已合）。  
> 本机 HEAD：`42ef593`（少 merge commit；功能与 main 同波次）。  
> 库：`runtime/stock_data.db` ≈ **12296.5 MB**。  
> 本文件冻结当日事实。实现 Agent 动手前应用命令复核，不得用更旧的 `STATUS.md` 当现状。

## 1. 一句话

工程骨架（P0–P6）已进仓库，生产旗标全关；核心 PIT 已回填但没收口；研究 600 股实验是 **FAIL**；运维 DAG/备份从未进入生产。  
离自己的「个人机构化」：差 **D 收口 + O 收口** 才能到工程就绪；差 **真实策略 edge** 才能到 READY。  
离 Wind/Bloomberg：产品类别不同，不是进度条。

## 2. 两层口径（禁止合成一个分数）

设计规格禁止「机构覆盖率 85%」这类不可复算百分比。只允许下面两层：

### 层 A — 项目自己的七闸门

判定器：`ab_screener/domain/readiness.py`

- 硬门 D/S/P/L/O/G 任一失败 → `BLOCKED`
- 仅 R 失败且其它全 PASS → `ENGINEERING_READY_RESEARCH_BLOCKED`
- 七项全 PASS 且证据身份一致 → 才可显示 `PERSONAL_INSTITUTIONAL_READY`
- Agent **不得**自行宣布 READY

### 层 B — 商业终端

v2 学的是工作流与控制原则，不是 Bloomberg AIM/PORT 或 Wind 的产品面。  
本仓库永久：无券商、无 OMS、无 LIVE、无多租户。禁止按商业功能表扩 scope。

## 3. 生产旗标（当日实测 `load_resolved_config`）

```text
V2_PIT_READ_ENABLED=false
V2_EXECUTION_WRITE_ENABLED=false
V2_STRATEGY_REGISTRY_ENABLED=false
V2_RISK_ENFORCEMENT_ENABLED=false
DAILY_SCHEDULER_ENABLED=false
INSTITUTIONAL_CONSOLE_V2_ENABLED=false
LIVE_TRADING_ENABLED=false          # 硬门，开则启动失败
V2_EXECUTION_DUAL_RUN_ENABLED=true  # 仅旧/新核心对比
```

含义：日常选股仍走旧路径。v2 页面（`/v2/desk` 等）存在，旗标关着就不能当生产控制台。

**实现 Agent 不得为了「看起来更机构」打开上述 false 旗标。** 开旗标是独立波次，见计划 Wave F，必须先有验收证据。

## 4. 七闸门当日判定

| 闸门 | 状态 | 证据摘要 | 距离 |
|---|---|---|---|
| D 数据/PIT | **INSUFFICIENT** | 核心四表已齐；fina/stock_basic/holder=0；cyq 约一半；公司行为账本 0；PIT 读旗标关；日线停在 20260818 | 收口，不是从零挖 |
| R 研究 | **FAIL / 未过** | 不是「没跑」。600 股 A/B 均为 FAIL；`research_candidates=0` | 最远；不是再写框架 |
| S 信号/策略 | PASS(实现) 生产未用 | `signal_observations=0` `scan_profiles=0` registry 关 | 中 |
| P 组合/风险 | PASS(实现) 仅 dual-run | 写账本/enforce 关 | 中 |
| L 账本 | 最接近 PASS | 纸面 10 个日结、3 单、1 成交；NO_REPLACE | 近 |
| O 运维 | **INSUFFICIENT** | `AB_BACKUP_ROOT` 空；`dag_runs=0`；调度关；无 ≥5 日 soak 证据 | 远 |
| G 治理 | PASS(实现) | LIVE 硬关；astock 不进 A 池；身份/文档未对齐 | 近 |

聚合：**BLOCKED**。

还到不了 `ENGINEERING_READY_RESEARCH_BLOCKED`，因为 D 和 O 没过。R 不过会继续挡住 READY。

## 5. 数据与 PIT（比 8-18 P8 证据更新）

`schema_migrations_v2` 已应用 11 个意图（含 `v2:pit_history`、`v2:aux_history`）。

| 数据集 | 完成分区 | 行数 | 备注 |
|---|---|---|---|
| daily | 976/976 | 5,182,998 | 齐 |
| daily_basic | 976/976 | 5,183,083 | 齐 |
| adj_factor | 976/976 | 5,230,324 | 齐 |
| moneyflow | 976/976 | 4,947,262 | 齐 |
| margin | 976/976 | 3,755,012 | 齐 |
| top_list | 967 | 72,179 | 基本齐 |
| cyq | 548/976 | 2,725,242 | 未完 |
| fina_indicator | — | **0** | 空；按 ts_code 分区 |
| stock_basic | — | **0** | 空 |
| holder | — | **0** | 空 |
| corporate_actions | — | **0** | 账本空 |
| pit_backfill_checkpoints | — | 6,395，status 全 done | 已跑过的分区 |

新鲜度：

- `daily` MAX = **20260818**（审计日 2026-08-21，滞后 3 个交易日）
- `scan_result` MAX = **20260814**
- 真实数据门禁最近一次：`runtime/gates/real_data_gate_20260818_190832.json` PASS（968 日、100 对源端 0 差）

P8 文档写「517 万行未跑」已经过时。当前 D 缺口是：**空表 + 新鲜度 + 读旗标未开**。

## 6. 研究（R）不是工程债

最近完整实验：

| run | 策略 | 结论 | 关键阻断 |
|---|---|---|---|
| `8131181b7843` 2026-08-16 | A | **FAIL** | OOS 净 PF 0.781；净回撤 95%；未优于随机 |
| `8f4adca67869` 2026-08-12 | B | **FAIL** | OOS 净回撤 68.6%；WF 仅 1/3 窗赚钱 |
| 其它 done 记录 | A/B | FAIL 或 INSUFFICIENT_EVIDENCE | 无候选 |

`research_candidates=0`，`research_trials=0`，`research_artifacts=0`（v2 治理表未进入真实产物链）。

可信报告目录：`runtime/v2/research/`，最近 `trusted_report_20260818_195921.json` 为 `INSUFFICIENT_EVIDENCE`（回撤与 WF 证据不完整）。

**实现 Agent 不得把 FAIL 改成 PASS、不得登记候选、不得让研究进 A 池或纸面买单。**

## 7. 纸面 / 信号 / DAG（生产未切换）

| 表 | 行数 | 含义 |
|---|---|---|
| pt_account | 1 | 有纸面户 |
| pt_cycle / pt_daily_snapshot | 10 | 有日结痕迹，**不是** DAG soak |
| pt_order / pt_fill | 3 / 1 | 体量很小 |
| dag_runs / dag_leases / dag_step_runs | 0 | 日调度从未跑 |
| audit_events | 0 | v2 审计链空 |
| signal_observations / signal_events / outcomes | 0 | v2 信号管线未用 |
| scan_profiles | 0 | 六形态方案未启用 |
| instrument_universe_rules | 5541 | 宇宙规则已投影 |

## 8. 工程债（G0–G8）当日

| Gate | 状态 | 证据 |
|---|---|---|
| G0 计划冻结 | 完成 | `docs/IMPROVEMENT-PROGRAM-2026-08-20.md` |
| G1 架构 `--strict` | **完成** | 2026-08-21 复跑 `architecture OK` exit 0 |
| G2 拆 `backend_app.py` | 未做 | 宿主仍约 **2648 行**；已有拆分方案 |
| G3 根脚本迁包 | 未做 | |
| G4 回测/扫描性能 | 未做 | |
| G5 CI 加严 | 局部脏 | 工作区 `ci.yml` 把 architecture 改成 `--strict`，**未进 main** |
| G6 文档索引 | 开始 | `docs/INDEX.md` 工作区未跟踪 |
| G7 Logic Platform | 代码在 | 必须保持 research_only |
| G8 P8 诚实验收 | 未关 | 仍 BLOCKED |

工作区脏文件（审计时）：

```text
 M .github/workflows/ci.yml
?? docs/INDEX.md
?? docs/superpowers/plans/2026-08-21-g2-backend-split.md
```

（本套 closers 文档写入后还会新增 `docs/superpowers/plans/2026-08-21-institutional-closers-*.md`。）

## 9. 相对商业终端（层 B，不要当 backlog）

| 商业终端 | 本仓库 | 处理 |
|---|---|---|
| 券商 OMS / 真实下单 | 永久禁止 LIVE | 不做 |
| L2 / 逐笔 | Tushare 日线+资金流+部分筹码/两融 | 不做 |
| 公告全文 / 新闻 / 一致预期 / 同花顺概念 | Token 权限不足 | 保持 INSUFFICIENT，禁止伪造 |
| 多用户 / RBAC / 多资产 / 厂商 SLA | 本机 SQLite 单用户 | 不做 |
| Barra 级生产风险 | 领域代码有协方差/MC/多因子 | 研究工具，不开成生产引擎 |
| astock PE 选股 | 只读情报桥已 ACCEPTED | **禁止**接入 A 池 / 扫描 / 纸面 |

## 10. 实现时不要被这些旧文档带偏

| 文档 | 问题 |
|---|---|
| `docs/STATUS.md` 日期 2026-08-11 | 写 pytest 350、九分闭环；未反映 v2。用户文档，勿覆盖 |
| P8 证据「PIT 517 万未跑」 | 核心表已跑完 |
| P8「架构债务 4 项」 | G1 `--strict` 已过 |
| astock 实现方自签 ACCEPTED | 已被独立检查覆盖；不要再改桥的写边界 |

## 11. 最短路径（给计划用）

1. **下一站（工程）**：D 收口 + O 收口 + 身份对齐 → 才可能 `ENGINEERING_READY_RESEARCH_BLOCKED`。  
2. **终点站（合同）**：上一站 + R 真实 PASS → 才可能 `PERSONAL_INSTITUTIONAL_READY`。当前 A/B 结果表明终点站没有工程捷径。  
3. 工程债 G2–G6 可与 D/O **并行**，但 **不能替代** D/O，也不能提前开生产旗标。
