# 阶段8 验收报告：质量门禁、发布和回滚 + 最终验收

日期：2026-08-08
状态：✅ 离线、本地运行与独立真实数据门禁全部通过

## 一、离线质量门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| 单元测试 | `python -m pytest` | ✅ 152 passed |
| Lint | `python -m ruff check .` | ✅ All checks passed |
| 类型 | `python -m mypy paper_trading web/backend_app.py tushare_init.py local_store.py` | ✅ 18 source files 无问题 |
| 前端 | `npm --prefix web/frontend run build` | ✅ 构建成功；纸面页拆为 17.51KB 独立 chunk |

新增 `pyproject.toml`（ruff 配置，选择性忽略 DTZ005/S110/RUF046 等历史风格项）；`requirements-dev.txt` 补充 ruff/mypy。

## 二、发布 / 回滚

- `docs/ROLLBACK-2026-08-07.md`：完整发布顺序 + 回滚方式 + 配置开关 + 数据字典 + 撮合假设
- `PAPER_TRADING_ENABLED=false` 可关闭纸面模块与调度器
- `LIVE_TRADING_ENABLED` 恒 false，代码无券商下单适配器
- 迁移仅前向执行；v7 对内部持仓批次表做数据保留的 CHECK 约束重建；portfolio.json 只读；账本只追加冲正

## 三、自动日终调度器

- `backend_app.py` 后台线程：交易日 16:15 后每分钟轮询
- 幂等：pt_cycle 已 DONE 的交易日跳过；每账户/交易日最多成功一次
- 应用重启自动补跑；`PAPER_TRADING_ENABLED=false` 时不启动

## 四、最终验收标准逐项核对

| 最终验收项 | 实现 | 验证 |
|---|---|---|
| A池信号→确认→次日撮合→持仓→损益→对账闭环 | scan_result→signal snapshot→draft→engine→settlement | ✅ 回归测试覆盖 |
| 固定历史行情重复运行结果一致 | 确定性撮合（开盘价±滑点） | ✅ test_double_run_no_duplicate_fills |
| 无未来函数 | signal available_at 门禁 + eligible_trade_date 严格晚于确认日 | ✅ 同日成交回归测试 |
| 现金与份额逐笔追溯 | pt_cash_flow running balance + FIFO 批次 | ✅ 对账 R1-R7；整批清仓归零 |
| 停牌/涨跌停/部分成交/T+1/费用/过期/公司行为失败模式 | engine + orders + settlement 测试 | ✅ 8+10+6 项测试 |
| 写接口幂等 + 结构化拒绝 | pt_api_idempotency 请求哈希 + DomainError | ✅ 回放/冲突/API 契约测试 |
| 离线质量门禁 | pytest/ruff/mypy/build | ✅ 全绿 |
| 真实数据门禁（730 交易日） | real_data_gate.py | ✅ 969 交易日；20×5 抽样 100 组零差异；公司行为可用 |
| 前端标识纸面仿真 | banner「纸面仿真交易—不会向券商下单」 | ✅ |
| 文档齐全 | 操作手册/验收/回滚/数据字典 | ✅ |

## 五、全部阶段交付清单

| 阶段 | 内容 | 验收 |
|---|---|---|
| 0 | 阻断修复（看门狗/版本/资金流/总览） | ✅ 227KB / 冷1.8s / 热0.2s |
| 1 | 迁移机制 + 15 领域表 + BEGIN IMMEDIATE | ✅ 1.23GB 真实库 schema v8 |
| 2 | 账户 + portfolio 预览确认导入 | ✅ 幂等/校验/不倒扣现金 |
| 3 | 草稿/确认/预交易风控 | ✅ 次日时点、原子预留、80/10/20 风险限额 |
| 4 | 撮合引擎 + FIFO 会计 | ✅ 整批/部分卖出及 FIFO 边界测试 |
| 5 | 日结/估值/对账 | ✅ 6 项测试 + 自动调度 |
| 6 | 前端工作台 /paper | ✅ 六块页面 + 键盘可操作 |
| 7 | 独立真实数据门禁 | ✅ 当前发布构建 PASS，报告已签名落库 |
| 8 | 质量门禁/发布/回滚 | ✅ 离线与真实数据门禁全绿 |

## 六、遗留事项

1. **真实数据门禁 PASS 报告**：当前代码版本 `8ec3906649d0` 的报告为
   `runtime/gates/real_data_gate_20260808_091422.json`，SHA-256：
   `836b4372e6df21c7d207c933b71b65f5fb3f1946bd52b218ecc257c302fd5cc6`。
2. **观察期**：启用自动日终调度后继续观察 ≥5 个交易日。

## 七、本轮纠错验收证据

- 修复同日收盘信号按同日开盘成交的未来函数路径；确认单只在严格下一开市日起可撮合。
- 卖出份额与买入现金预留改为 `BEGIN IMMEDIATE` 内原子复核，重复卖出确认稳定拒绝。
- 对账 `DIFF` 不再写日结快照或 `DONE`；循环停在 `RECONCILE` 并保留阻断原因。
- 未处理公司行为在任何撮合/估值写入前阻断；应用调整后可幂等重跑。
- 所有纸面 POST 要求 `Idempotency-Key`；旧 `POST /api/portfolio` 返回只读迁移错误。
- `PAPER_TRADING_ENABLED=false` 同时关闭路由与调度；`LIVE_TRADING_ENABLED=true` 拒绝启动。
- 真实日线已增量同步至 20260807，最新日写入 daily/daily_basic/moneyflow 各 5,535 行。
- 浏览器验收：1280px 与 390px 无文档级横向溢出、控制台零 error/warn、键盘焦点可见。
- 接口实测：overview 242,767 bytes，冷 781ms/热 95–98ms；dashboard 9–12ms。
- schema v7 修正持仓批次完整核销约束；200 股整批卖出后批次保留且 `remaining_qty=0`，外键检查无异常。
- Tushare 调用统一收口到 `tushare_init.py`；项目 `.env` 覆盖父进程陈旧凭据，自定义网关保留尾部 `/`，瞬态非 JSON 响应有界重试。
- schema v8 补齐所有日线 PIT 元数据；当前缺失计数为 0，真实门禁 PASS。
