# V2 remediation 独立总验收

日期：2026-08-28

项目：`accumulation_breakout`（AB-Screener · 横盘吸筹突破）

当前发布候选源码身份：`2031bdc70b61`；平台配置：`a958f710d75ce51d`

运行入口：`http://127.0.0.1:8001/`
最终裁决：**BLOCKED**

## 1. 管理结论

三波工程任务、组合级研究纠错、恢复续验和凭据脱敏均已通过代码级验收，但“工程实现完成”
不等于“七闸门通过”。当前 R 为策略事实 FAIL，O 因备份 5/7 与 soak 1/5 未成熟，S 也需
真实时间积累；D/P/L/G 的上一生产身份为 PASS，当前候选身份的运行证据正在重新绑定。总裁决保持
`BLOCKED`，不得标记 `PERSONAL_INSTITUTIONAL_READY` 或实盘就绪。

系统身份已经纠正并固定为 Breakout，不是 AETF：服务端返回
`product=accumulation_breakout`、`display_name=AB-Screener · 横盘吸筹突破`、默认端口 8001；
`LIVE_TRADING_ENABLED=false`，没有券商下单能力。AETF/8000 未被本次验收修改。

## 2. 七闸门现场结果

| 闸门 | 结果 | 现场证据与阻断 |
|---|---|---|
| D 数据/PIT | **PASS** | 当前构建真实数据门禁 PASS；969 个完成交易日，本地/源端/沪深300最新日 20260827；100 对源端抽样 0 差异；三类最新 PIT 分区全部零漂移。 |
| R 研究 | **FAIL** | 当前身份权威任务 `v2auth20260828e` 完整完成且可复算；OOS 组合净收益 8.49%、PF 1.223、最大回撤 9.18%，但 PBO 31.25%、DSR 46.36%、MinTRL 覆盖 18.8%、嵌套窗仅 1/5 正收益，`candidate_eligible=false`。 |
| S 策略/信号 | **INSUFFICIENT** | 六插件已接入生产 SHADOW；`signal_observations=52`、5 类策略、`signal_outcomes=0`。观察日期尚未达到 5/10/20 日成熟线，禁止伪造 outcome。 |
| P 组合/风险 | **PASS** | 最新交易日风险快照 `955681b4321b58f6` 已固化，行情/规则/配置版本齐全；统计状态如实为 INSUFFICIENT（权益序列不足 30 点）。 |
| L 账本/日清 | **PASS** | 扫描 `eaf90d4808c6`、DAG 9/9 COMPLETED、周期 DONE、对账 OK、日清清单 COMPLETE；清单哈希 `5541d057079a...`。 |
| O 运维/恢复 | **INSUFFICIENT** | 16.5GB 严格恢复完整性/FK/双 SHA 全过，RTO 1789.798/1800 秒；但可验证备份仅 5/7，当前身份 soak 1/5，均未达到成熟线。 |
| G 治理/安全 | **PASS** | 实盘关闭、审计 hash chain、DB 外签名锚点、HTTPS 实际 API 与独立原生 TLS 探针均通过；最新探针时间 `2026-08-28T01:11:27+08:00`，证据 SHA-256 `1741dd66136e...`，无 HTTP 回退。 |

最终门禁文件在文档提交后按最终 Git 身份重新生成；`GET /api/v2/readiness` 的服务端聚合必须
与上表一致。浏览器不能传入布尔值覆盖闸门。

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
`appended_revisions=0`。20260827 行数分别为 daily 5549、daily_basic 5547、moneyflow 5547，
每类 canonical 与最新 PIT 行数相同。

### 3.2 迁移与生产库安全

- 修复迁移 checksum 对 worktree 绝对路径敏感的问题；未知源码漂移仍拒绝。
- 使用 SQLite online backup 生成压缩备份；最终口径以完整性/FK、归档 SHA-256 与逻辑库
  字节级 SHA-256 验证：`E:/ab-backups/backup_20260827_231331.db.gz`，归档
  4,261,575,768 bytes，逻辑库 16,465,776,640 bytes。
- 先在 16GB 校验副本迁移两次（第一次应用、第二次 no-op），再在生产库执行同一迁移；
  `PRAGMA quick_check=ok`，原行情、扫描和纸面账本关键计数保持。
- 本轮只追加 PIT 修订、同步当前行情/基础/资金流/基准以及写真实门禁报告；没有删除或重写
  订单、成交、现金流水、持仓批次或旧异常记录。

### 3.3 UI 安全降级

Playwright 首轮发现平台状态接口返回部分 payload 时，侧栏直接读取缺失的 `flags` 导致整页
崩溃。已改为 fail-closed：缺 flags 时隐藏受控导航而不崩溃。修复后页面导航、刷新恢复、
390px 窄屏和键盘焦点 4/4 通过。

### 3.4 恢复中断与 RTO

- 首次恢复进程被会话中断后只留下私有 `.partial`，没有覆盖目标或生产库。
- 新增中断后安全续验：仅接受目标同目录、目标专属命名、零 WAL 的候选；重新验证归档 SHA、
  SQLite `integrity_check`、外键与完整逻辑库 SHA 后才原子落目标。
- RTO 从首次启动 `2026-08-27T23:48:13+08:00` 计时，最终 1789.798 秒，低于 1800 秒上限，
  但余量仅 10.202 秒，列为运维性能风险。

### 3.5 凭据回显纠错

- 一次供应商拒绝响应使用新措辞回显凭据样式内容，旧正则未覆盖；扫描正确失败且没有固化结果。
- 新增无 SDK 依赖的统一文本脱敏，在供应商异常、扫描子进程 JSON、持久任务和 API 返回四个边界使用。
- 已清理该失败任务数据库字段及两个运行时 JSON；保留失败状态和诊断语义，不删除失败记录。

## 4. 质量证据

权威运行时：`E:/CODEX/Stock_selection/accumulation_breakout/.venv312/Scripts/python.exe`
（Python 3.12）。

| 检查 | 结果 |
|---|---|
| `scripts/quality_gate.ps1 -Strict` | PASS：Ruff、Mypy、strict architecture、928 个离线测试、前端构建全绿 |
| 全收集 `python -m pytest -q` | **928 passed in 210.69s** |
| PIT/数据专项 | **70 passed** |
| Vitest | **7 passed** |
| Playwright | 首轮 1/4（暴露崩溃）；修复后 **4/4 passed** |
| npm audit | **0 vulnerabilities** |
| Vite production build | 700 modules；主包 gzip 75.90KB |
| ECharts 独立 chunk | 687.38KB / gzip 230.08KB，性能警告，非本轮功能阻断 |

真实数据门禁报告：

- `runtime/v2/real-data-gates-final/real_data_gate_20260828_010252.json`
- 构建/数据配置/数据库：`7940f508fb00` / `745a7010eae38014` / `5e4570636a4e7386`（上一生产身份；当前候选须重新生成）
- 报告签名：`c8180918bc2f1c55ee8fa8abb5978a8218989925d4cef3ba49843f9a55d33b62`

该报告属于上一生产身份，只能证明数据事实，不得冒充当前候选身份的发布证据。当前候选重建后，
`/api/v2/readiness` 仍须验证 D=PASS、`identity_blockers=[]`。旧 `/api/release/readiness` 只表示代码/配置/数据库与真实数据报告
满足数据发布证据，不代表七闸门或策略 edge 通过；平台总裁决只以 V2 readiness 为准。

## 5. 剩余工作（不能伪造）

1. 等 52 条 SHADOW 观察自然达到 5/10/20 日并产生真实 outcome；不足长期阈值前 S 保持
   INSUFFICIENT，所有插件继续 EXPERIMENTAL。
2. 再新增 2 个真实恢复点达到验证备份 7/7；严格恢复本身已 PASS。
3. 再积累 4 个不同真实完成交易日达到 soak 5/5；不得用历史回填或多次同日运行冒充。
4. R 已有当前身份完整证据但策略结论 FAIL；若要通过，只能提出新假设并重新预登记研究，
   不得事后调阈值或复制旧 PASS。

## 6. 回滚

- 关闭 V2 能力：保持 `V2_PIT_READ_ENABLED=false`、`V2_EXECUTION_WRITE_ENABLED=false`、
  `V2_RISK_ENFORCEMENT_ENABLED=false`、`DAILY_SCHEDULER_ENABLED=false`。
- UI/同步纠错按提交整体 `git revert`；PIT history 不删除，以追加冲正说明回滚。
- 生产库可从上述验证备份恢复到迁移前状态；恢复必须使用既有严格恢复脚本并在临时位置先验。
- `LIVE_TRADING_ENABLED` 始终为 false；本项目不提供券商适配器。

本报告接受三波工程交付，但 P8 最终验收保持 **BLOCKED**。
