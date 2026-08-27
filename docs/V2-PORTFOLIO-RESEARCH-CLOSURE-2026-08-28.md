# Breakout V2 组合回测与研究收尾工作包

日期：2026-08-28

产品：`accumulation_breakout`（AB-Screener · 横盘吸筹突破）

## 1. 纠错结论

当前权威研究 `v2auth20260828b` 的逐笔成交、下一交易日开盘、滑点和费用证据有效，且结论
必须继续保留为 `FAIL`。但其 `net_max_drawdown` 将每笔成交按固定 10 万元、100% 资金串行
复利；同一时段重叠交易没有共享现金、持仓和总仓位约束。该口径适合单笔压力诊断，不是
最终产品的组合权益曲线，不能通过放宽 25% 回撤阈值来修饰。

因此下一轮先修正组合会计，再进行新的预登记研究。旧报告不可覆盖，新报告必须携带新的
组合模型版本、代码版本、数据版本和研究 ID。

## 2. 冻结组合模型 V2

- 初始研究权益：100 万元人民币，整数分记账。
- 单标的目标权重上限：10%。
- 总持仓上限：80%。
- 下单后保留现金：至少决策前权益的 10%。
- 单日新增买入上限：决策前权益的 20%。
- 同一标的只允许一个活动持仓，不做空。
- 买入按信号后的下一可交易日开盘；卖出沿用冻结的出场事件，无法成交时顺延到下一可交易日。
- 数量按 instrument lot 向下取整；成交量参与率默认 5%。
- 费用、滑点、涨跌停、停牌和零/部分成交复用 V2 整数执行核心。
- 每个交易日按收盘价逐日估值；回撤只从组合权益曲线计算。
- 同日存在多个新信号时，按稳定键排序并平均分配当日新增预算；不得让每笔交易各自占用 100% 权益。
- 旧的逐笔 PF、胜率和单笔收益继续作为诊断字段，不能冒充组合收益或组合回撤。

## 3. 防未来函数与研究纪律

- 组合会计纠错不改变形态参数，也不读取 OOS 结果选择仓位参数。
- 当前历史窗仅用于验证实现与生成新的、明确披露的研究证据；不得把修正后更好看的曲线直接
  描述为新 edge。
- 正式 R 门还必须把研究行情读取切到 `available_at <= decision_at` 的 PIT 路径，并完成固定
  前向确认期；在此之前结论最多为 `INSUFFICIENT` 或 `FAIL`。
- 下一轮不降低现有净 PF、25% 最大回撤、WF、双基线、DSR 或 MinTRL 阈值。
- `PASS` 也只允许登记隔离候选，不自动进入 A 池或生成订单。

## 4. 影响文件

- 新增 `ab_screener/research/portfolio_accounting.py`
- 修改 `optimizer.py`
- 修改 `ab_screener/research/backtest_engine.py`
- 修改 `ab_screener/research/trusted_run.py`
- 修改可信报告渲染与类型契约（如实际需要）
- 新增 `configs/research/portfolio_v2.yaml`
- 新增 `tests/test_research_portfolio_accounting.py`
- 更新 `tasks/backlog.yaml`、`tasks/implementation_state.yaml`、`docs/STATUS.md`

## 5. 验收标准

1. 两笔同日信号不会各自消耗全部账户权益；现金永不为负。
2. 买入后总仓位不超过 80%，单标的不超过 10%，单日新增不超过 20%。
3. 整手、停牌、一字涨跌停、参与率、现金不足和重复持仓均有正常及失败测试。
4. 每日权益满足 `cash + Σ(qty × close)`，最大回撤由逐日权益复算误差为零分。
5. 组合模拟重复运行产生相同成交、拒绝、费用和权益 SHA-256。
6. 研究报告同时展示逐笔诊断和组合指标，并明确门禁读取哪个字段。
7. 旧 `v2auth20260828b` 报告和数据库记录不删除、不重写。
8. 新权威研究必须重新预登记；当前配置不得在新报告完成前改绑权威 ID。
9. 后续 PIT 研究读取必须有修订前后与 `available_at` 边界测试。
10. Ruff、Mypy、Pytest、严格架构检查和前端构建通过。

## 6. 回滚

组合模型以版本字段和配置开关隔离。若新模型失败，权威研究继续保持
`v2auth20260828b = FAIL`，纸面账本、订单、成交、持仓和生产扫描均不回滚、不重写。

## 7. P7.1 完成证据

- 组合模型：`research-portfolio-v2.0.0`，配置文件 `configs/research/portfolio_v2.yaml`。
- 权威门禁字段由共享账户逐日权益产生；旧逐笔结果仅保留为 `trade_*` 诊断。
- IS/OOS、三窗 WF、随机基线、MA20/60 基线和单组回放均已接入相同组合模型。
- 严格质量门：Ruff、Mypy、strict architecture、903 个离线测试、前端生产构建全部通过。
- 旧权威报告保持不变；新报告尚未生成，R 门仍为 `FAIL`。
- 运维备份证据已推进到 `5/7`，Soak 仍为 `1/5`，不得提前宣布最终就绪。

## 8. P7.2 PIT 研究读取实施方案

`available_at` 表示本机真实获得该修订的时刻；历史回填数据不会伪装成历史当日已可用。
权威研究因此冻结一个预登记的 `knowledge_cutoff_at`，只读取
`available_at <= knowledge_cutoff_at` 的最大 revision；策略本身仍只用信号日及以前 K 线，
并在下一交易日开盘成交。报告必须同时披露知识截止点和模拟决策/成交时点，不能混称。

影响文件：

- 新增 `ab_screener/research/pit_reader.py`：批量 PIT 修订、历史生命周期宇宙、覆盖核对、快照 hash。
- `optimizer.py`、`walkforward.py`、`backtest_engine.py` 接收冻结研究快照，不再自行读取 `daily` 投影。
- `trusted_run.py`、真实研究脚本与 Lab 启动接口把 PIT 版本、截止点、宇宙 hash 和数据 hash 纳入输入身份。
- 新增 `tests/test_research_pit_reader.py`，覆盖修订边界、未来修订不可见、当前宇宙不得回填、缺记录/篡改 fail-closed。

验收：

1. 截止点前后读取同一业务键分别得到旧/新 revision。
2. `daily` 中在截止点可见的业务键若无 PIT 历史，整次研究拒绝启动。
3. 股票宇宙来自 `instrument_lifecycle_history` 在截止点可见的修订，并按研究窗口生命周期相交过滤。
4. 网格、OOS、WF、双基线和正式统计只消费同一个冻结快照。
5. 新研究身份包含 PIT reader 版本、截止点、宇宙 SHA-256、数据 SHA-256；旧缓存不得复用。
6. 当前旧报告保持 `FAIL`，完成新预登记运行前不改 `configs/platform_v2.yaml` 的权威 ID。

## 9. P7.2 生产复算发现的 T+1 边界纠错

PIT 小样本复算发现：当信号位于研究窗倒数第二个交易日时，旧 `trade_sim` 会把次日开盘
买入和同日收盘退出同时记为完成交易。该路径不满足 A 股股票买入后下一交易日才可卖，且
会令组合账留下无法执行的未平仓。因此必须在新权威研究前修正，旧报告继续保持不可变。

影响文件：

- `trade_sim.py`：止损/目标最早从买入后的下一交易日检查；需要次日开盘的退出在缺少次日
  行情时返回未完成，不再回退到确认日收盘。
- `ab_screener/research/portfolio_accounting.py`：候选必须满足
  `signal_date < entry_date < exit_date`，作为第二层 fail-closed 防线。
- `tests/test_bench_volume.py`、`tests/test_research_portfolio_accounting.py`：覆盖买入日触发止损、
  研究窗末端仅有买入日、同日退出候选三类边界。

验收：

1. 买入日内低点即使触及止损，也不能产生同日卖出；最早在下一交易日执行。
2. 研究窗只剩买入日时，该信号不是完成交易，不进入收益、PF 或组合成交统计。
3. 组合会计收到同日退出候选时明确拒绝，不能静默留下仓位。
4. 修正后的权威研究使用新代码、执行模型与组合配置 hash 重新预登记；旧报告不覆盖。

## 10. P7.2 完成证据

- PIT reader：`research-pit-reader-v2.0.0`；knowledge cutoff 固定为带 `+08:00` 时区的
  `decision_at`，同一业务键只取该时点可见的最大 revision。
- 生产数据库只读复算：600 标的、538,566 行、964 个交易日；宇宙 SHA-256
  `bb41c6775f61c7ac480607860e17edf573b856290487141a3da1abc4b92533a3`，数据指纹
  `faf588403e3db351`。
- T+1 边界样本已纠正：20 标的 OOS 小样本为 10 个完成候选，组合 7 买 7 卖、0 未平仓，
  `portfolio_status=PASS`。
- 执行血缘升级为 `v2.1.1`；滑点报告按实际成交价差计算，现金仍只受成交价一次影响；
  组合配置 hash 为 `8457421182d765e6`。
- 严格质量门通过：Ruff、Mypy、strict architecture、916 个离线测试、前端生产构建。
- 旧权威研究 `v2auth20260828b` 仍为 `FAIL` 且未覆盖；下一步必须以本次新 build 重新预登记，
  不得降低 PF、回撤、WF、双基线、DSR 或 MinTRL 门槛。

## 11. P7.3 正式晋级门接线纠错方案

`v2auth20260828d` 在 PIT、组合账户、OOS、三窗 WF 和双基线层得到传统门禁 `PASS`，但同一
报告的正式统计为 `DSR=0`、`MinTRL coverage=0.188`。现有流程在生成正式统计前已经设置
`candidate_eligible=true`，导致正式统计只展示、不阻断，这是不可接受的可信性缺口。该运行
保留为诊断证据，不得写入 `configs/platform_v2.yaml` 的权威 ID。

实施顺序：

1. 先将 `ROBUST_PERSONAL_V2` 接为最终硬门；传统门通过但正式证据缺失或失败时，最终结论
   必须为 `FAIL/NO_CANDIDATE`。
2. 统一报告、数据库 `candidate_eligible/can_claim_edge`、候选登记与前端摘要，禁止出现两个
   互相冲突的结论。
3. 报告在正式统计和晋级决策完成后再渲染 Markdown；写入传统门与正式门的独立明细。
4. 预登记主基线固定为 `ma20_60`；PBO、至少 5 个外层测试窗、2×成本及参数邻域证据缺失
   都按失败处理，不用默认值伪造。
5. 补齐上述证据计算后，以新 run ID 重跑；旧 `v2auth20260828b/c/d` 均不覆盖。

验收：

- 构造“传统 PASS、DSR FAIL”时，最终 `candidate_eligible=false`，且不会写研究候选。
- 构造正式证据缺字段时明确列出阻断项，而不是降级为传统 PASS。
- 只有 `ROBUST_PERSONAL_V2` 全部门槛通过才返回 `CANDIDATE`；`STRICT_RESEARCH_V2` 独立展示。
- 报告 JSON、Markdown、研究运行表和 API 恢复后的结论完全一致。

## 12. P7.3 实现口径与离线验收

本轮没有降低任何晋级阈值，而是把原先仅展示的统计量改成最终硬门：

- `formal-evidence-v2.0.0` 从 IS 全网格的共享账户每日净收益构造对齐矩阵；无持仓日按现金
  零收益对齐，并固化日期、参数和矩阵 SHA-256。
- CSCV-PBO 每个训练切分只冻结 Sharpe 第一名，再在互斥测试切分读取一次排名；组合数过大
  时确定性等距抽取最多 2,000 个切分，抽取规则写入报告。
- 嵌套参数 Walk-forward 使用扩展训练窗和 5 个互不重叠测试窗；每窗只以训练期复合收益
  选择参数，测试期不能反向替换参数。
- 参数邻域在读取 OOS 前由 IS 第一名和预登记网格确定，只允许一个坐标变化；任一计划邻域
  缺少结果都会使覆盖率低于 100%，从而阻断晋级。
- 2× 成本通过 `research-portfolio-v2.1.0` 的 `cost_multiplier_bps=20000` 重放冻结候选及
  预登记主基线；佣金率、最低佣金、税费、其他费和滑点假设同时放大，配置 hash 独立固化。
- DSR 的零假设最大 Sharpe 按 Bailey 与 López de Prado 的原始口径使用全部 IS 参数 Sharpe
  的横截面标准差；多试验但缺少该分散度时返回 `INSUFFICIENT`，不再用无尺度常数替代。
- `formal-promotion-gate-v2.0.0` 只有在传统门与 `ROBUST_PERSONAL_V2` 同时通过时才设置
  `candidate_eligible=true`；`STRICT_RESEARCH_V2` 只作为独立对照。

离线验收证据：

- Ruff：PASS。
- Mypy：PASS。
- 严格架构检查：PASS。
- Pytest：`928 passed`。
- 前端 TypeScript 与 Vite 生产构建：PASS。
- 旧 `v2auth20260828b/c/d` 保持不可变；新的权威 ID 必须在本实现固化后重新预登记。

回滚：恢复上一提交即可回到只展示正式统计的旧流程；不得修改既有研究行或删除失败报告。
无论回滚与否，`LIVE_TRADING_ENABLED=false` 和“研究候选不自动进入 A 池/订单”均保持不变。
