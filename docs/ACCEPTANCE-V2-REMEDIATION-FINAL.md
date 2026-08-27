# V2 remediation 独立总验收

日期：2026-08-27

项目：`accumulation_breakout`（AB-Screener · 横盘吸筹突破）

候选代码头：`fab734094b8146a7eb76485bd484e10346c9e866`

运行入口：`http://127.0.0.1:8001/`
最终裁决：**BLOCKED**

## 1. 管理结论

三波工程任务已经完成并通过代码级验收，V2R-G 共享入口也已接受；但“工程实现完成”不等于
“七闸门通过”。当前只有 D 数据闸门 PASS，R/S/P/L/O/G 仍有真实证据缺口或硬失败，
因此不能标记 `ENGINEERING_READY_RESEARCH_BLOCKED`，更不能标记
`PERSONAL_INSTITUTIONAL_READY`。

系统身份已经纠正并固定为 Breakout，不是 AETF：服务端返回
`product=accumulation_breakout`、`display_name=AB-Screener · 横盘吸筹突破`、默认端口 8001；
`LIVE_TRADING_ENABLED=false`，没有券商下单能力。AETF/8000 未被本次验收修改。

## 2. 七闸门现场结果

| 闸门 | 结果 | 现场证据与阻断 |
|---|---|---|
| D 数据/PIT | **PASS** | 968 个完成交易日；最新本地/源端/沪深300均为 20260826；20 标的×5日=100 对抽样 0 差异；daily/daily_basic/moneyflow 最新分区均 0 缺历史、0 内容差异、0 元数据差异。 |
| R 研究 | **INSUFFICIENT（已知历史结论 FAIL）** | 生产库不存在配置的权威任务 `0746a4108e15`，且旧证据代码身份不是当前构建。已保存的权威历史实验本身为 FAIL：OOS PF 1.112、最大回撤 88.03%、PBO 0.3815、DSR 0，不可晋级。 |
| S 策略/信号 | **INSUFFICIENT** | 六插件工程契约通过，但生产库 `strategy_profiles=0`、`signal_observations=0`、`signal_outcomes=0`；没有成熟 Shadow/Paper 或 `ACTIVE_FOR_A_POOL` 证据。 |
| P 组合/风险 | **INSUFFICIENT** | 风险算法、手算和故障 fixture 通过，但生产 `risk_snapshots=0`，不能把离线测试等同于生产风险闸门。 |
| L 账本/日清 | **FAIL** | 最近清单为 20260821 `PARTIAL`，阻断 `MISSING_SCAN_RUN`；20260826 没有 `COMPLETE` 日清单。最近周期和对账虽为 DONE/OK，仍不能抵消 L-12。 |
| O 运维/恢复 | **FAIL** | 可验证备份 3/7；当前身份真实完成交易日 soak 0/5。五日观察不能在一次实现会话中压缩或回填。 |
| G 治理/安全 | **FAIL** | clean 身份、质量门、端口隔离、实盘关闭、前端 E2E 已通过；但供应商地址仍为明文 `http://a.sszhixia.cn/`，按 G-14 必须失败；生产审计链也尚无充分事件/外部锚点证据。 |

`GET /api/v2/readiness` 的服务端聚合与上表一致，状态为 `BLOCKED`；浏览器不能传入布尔值
覆盖闸门。

## 3. 本轮纠错实现

### 3.1 数据双路径漂移

旧同步只覆盖 canonical `daily/daily_basic/moneyflow`，没有同步追加 PIT history，导致新日期
以及供应商修订在正式 PIT 读开启后会走出两套事实。已完成：

- 新增按交易日分区的 canonical + PIT 原子写入；失败整笔回滚；历史表保持 append-only。
- 遗留 canonical 值先作为 `canonical_recovery:*` 保存，再写数据源修订，旧值不删除。
- 同内容重跑不追加修订；整数 `0` 与 SQLite `REAL 0.0` 统一业务哈希，避免伪修订。
- daily、daily_basic、moneyflow、沪深300基准全部接入同一写入路径。
- 16:15 前不把当日视为完成；每次同步强制复核最近 5 个完成交易日。
- 真实门禁新增最新完成交易日三类 canonical/PIT 全分区一致性检查。

生产库连续复核两次的第二次结果为：四类 `canonical_updated=0`，四类
`appended_revisions=0`。20260826 行数分别为 daily 5549、daily_basic 5547、moneyflow 5547，
每类 canonical 与最新 PIT 行数相同。

### 3.2 迁移与生产库安全

- 修复迁移 checksum 对 worktree 绝对路径敏感的问题；未知源码漂移仍拒绝。
- 使用 SQLite online backup 生成备份；最终口径以完整性/FK、归档 SHA-256 与逻辑库
  字节级 SHA-256 验证（旧逐表 JSON 哈希因超过恢复 RTO 已退役）：
  `E:/ab-backups/backup_20260827_084523.db`，16,324,935,680 bytes。
- 先在 16GB 校验副本迁移两次（第一次应用、第二次 no-op），再在生产库执行同一迁移；
  `PRAGMA quick_check=ok`，原行情、扫描和纸面账本关键计数保持。
- 本轮只追加 PIT 修订、同步当前行情/基础/资金流/基准以及写真实门禁报告；没有删除或重写
  订单、成交、现金流水、持仓批次或旧异常记录。

### 3.3 UI 安全降级

Playwright 首轮发现平台状态接口返回部分 payload 时，侧栏直接读取缺失的 `flags` 导致整页
崩溃。已改为 fail-closed：缺 flags 时隐藏受控导航而不崩溃。修复后页面导航、刷新恢复、
390px 窄屏和键盘焦点 4/4 通过。

## 4. 质量证据

权威运行时：`E:/CODEX/Stock_selection/accumulation_breakout/.venv312/Scripts/python.exe`
（Python 3.12）。

| 检查 | 结果 |
|---|---|
| `scripts/quality_gate.ps1 -Strict` | PASS：Ruff、Mypy、strict architecture、868 个离线测试、前端构建全绿 |
| 全收集 `python -m pytest -q` | **900 passed in 281.45s** |
| PIT/数据专项 | **70 passed** |
| Vitest | **7 passed** |
| Playwright | 首轮 1/4（暴露崩溃）；修复后 **4/4 passed** |
| npm audit | **0 vulnerabilities** |
| Vite production build | 700 modules；主包 gzip 75.90KB |
| ECharts 独立 chunk | 687.38KB / gzip 230.08KB，性能警告，非本轮功能阻断 |

真实数据门禁报告：

- `runtime/gates/real_data_gate_20260827_111047.json`
- 构建/配置/数据库：`a2f99ff8cb9c` / `745a7010eae38014` / `d4cefc68843a30a0`
- 报告内签名：`2b2e7a27c9b7121b628ec3435d8e8eb57dd9a83980a88970350c4ae8546e6359`
- 文件 SHA-256：`6ae76c952775680d48f5e26a4f7bfe9e935ae6f054a9d701145b0c450dc6f716`

该报告是在最终前后端构建重启后生成；`/api/v2/readiness` 已验证 D=PASS、
`identity_blockers=[]`。旧 `/api/release/readiness` 只表示代码/配置/数据库与真实数据报告
满足数据发布证据，不代表七闸门或策略 edge 通过；平台总裁决只以 V2 readiness 为准。

## 5. 剩余工作（不能伪造）

1. 将 Tushare 自定义节点置于 HTTPS 或可信 TLS 隧道后，重跑 G 安全验收。
2. 在当前代码、配置、数据、成本和撮合身份上重新预登记并运行权威研究；保持 FAIL 也可以，
   但必须进入生产事实库并可由 readiness 读取，禁止拷贝旧 PASS 或事后调阈值。
3. 正式注册可观察策略并积累 Shadow/Paper outcome；未达到 S 的月数、样本和双基准阈值前，
   策略保持 EXPERIMENTAL。
4. 对最新完成交易日完成扫描→周期→风险快照→对账→`daily_run_manifest COMPLETE`，消除
   `MISSING_SCAN_RUN`。
5. 累积至少 7 份验证备份，完成一次严格临时恢复，并按真实交易日累计 5/5 soak。
6. 产生生产写操作审计链、DB 外 chain-head 锚点，并完成敏感信息与本机网络复验。

## 6. 回滚

- 关闭 V2 能力：保持 `V2_PIT_READ_ENABLED=false`、`V2_EXECUTION_WRITE_ENABLED=false`、
  `V2_RISK_ENFORCEMENT_ENABLED=false`、`DAILY_SCHEDULER_ENABLED=false`。
- UI/同步纠错按提交整体 `git revert`；PIT history 不删除，以追加冲正说明回滚。
- 生产库可从上述验证备份恢复到迁移前状态；恢复必须使用既有严格恢复脚本并在临时位置先验。
- `LIVE_TRADING_ENABLED` 始终为 false；本项目不提供券商适配器。

本报告接受三波工程交付，但 P8 最终验收保持 **BLOCKED**。
