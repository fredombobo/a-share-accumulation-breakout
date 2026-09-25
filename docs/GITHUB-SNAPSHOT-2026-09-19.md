# AB-Screener 个人研究开发快照（2026-09-19）

本次发布保留每日选股、专业回测、前向观察与研究诊断的现有实现，定位为个人研究开发快照。
工程检查通过不能替代收益证据，也不代表稳定盈利、已验证跑赢市场或机构验收通过。
产品仍为 `accumulation_breakout` / 8001，`LIVE_TRADING_ENABLED=false`，不启用纸面交易或机构运维流程。

## 纠错重放的证据对账

成交模型已为 `v2.1.3`，组合模型为 `research-portfolio-v2.2.0`；
`tests/test_execution_corrections_20260913.py` 覆盖跳空止损、真实开盘一字板、资金时序与会计边界。
不能继续把这项工作概括为“纠错尚未实施”。

前端归档 `web/frontend/public/reliability/replay-summary.json` 声明于
`2026-09-14T00:15:33+08:00` 完成，研究代码身份为 `c3a798b31cc7`，并保留四臂摘要：

| 原始 run_id | 纠错摘要 run_id | 摘要中的纠错后 OOS 净收益 |
| --- | --- | ---: |
| probt-dual-v1-control | probt-rel20260913-v1-dual-v1-control | -6.171539% |
| probt-dual-v1-momentum | probt-rel20260913-v1-dual-v1-momentum | -2.730647% |
| probt-mom6m-v1-control | probt-rel20260913-v1-mom6m-v1-control | -5.025578% |
| probt-mom6m-v1-momentum | probt-rel20260913-v1-mom6m-v1-momentum | 0.026674% |

2026-09-19 通过 SQLite `mode=ro` / `query_only` 读取本机 AB 研究库：

- `runtime/stock_data.db` 中四个原始 run_id 均存在且为 `done`；双创代码身份
  `190aa017d295`，mom6m 代码身份 `f75ac755a527`，数据版本均为 `2026-09-05T17:13:39+08:00`。
- 该库及 `runtime/lhb_product.db` 均未找到上述四个纠错 run_id；已检查的本机运行产物与
  Codex 可视化产物中也未找到对应原始报告。
- 摘要中的 `accounting_passed=true`、原参数及原记录不变属于归档声明，尚不能在本次复核中
  独立确认纠错任务的数据身份、逐笔会计、完整 OOS/WF、双基线与成本压力结果。

因此当前登记为：**纠错实现完成、摘要可用、最终证据验收仍为 EVIDENCE_INCOMPLETE**。
后续需找回四份原始纠错报告与输入身份再复算；不替换旧失败工件，不以其它 PASS 任务替代权威失败研究，
不把重复回放当作新样本外证据，不自动晋级策略。

## 推送范围与复核

- 保留现有代码、测试、文档及配套前端 `dist`；数据文件、真实 `.env`、日志及运行证据不入库。
- 日历测试使用明确上海时区，并单独保留旧版无时区输入的 16:00 边界；直接调用真实评分函数，去掉 AST/`exec`。
- `.pytest_tmp/` 是本地测试产物，加入 Git/Ruff 排除；没有递归删除目录或放松业务门禁。
- 当前数据网关按既有明确配置使用 `http://a.sszhixia.cn/`，该连接不经过 TLS；其它 HTTP 节点及重定向继续拒绝。
- GitHub CI 覆盖既有发布分支 `收口-20260831`，Mypy 范围与本地严格质量门对齐。

本次 Python 3.12.10 本地严格质量门已通过：Ruff、Mypy（42 个模块）、严格架构检查、
1564 项 Python 测试、108 项前端测试（14 个文件）、TypeScript 检查及 Vite 构建。
另外，74 项针对性测试先行通过；构建入口与所有本地分块引用完整，`public` 与 `dist` 中对应资源一致。
构建仅保留 ECharts 分块大于 500 kB 的体积提示，不影响构建成功。

只读身份接口复核为 `product=accumulation_breakout`、`default_port=8001`、`live=false`；
研究就绪度仍为 `BLOCKED`，未重启服务、触发扫描/重放或改变门禁。
敏感信息检查的私有复核细节保存在被忽略的本机产物中；当前文件与历史记录分别记录检查结论。
云端 CI 以本次推送提交对应的 Actions 结果为准。
