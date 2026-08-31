# 龙虎榜研究产品运行手册

> 状态：本地隔离副本产品可用；研究结论仍为 `RESEARCH_BLOCKED`。本手册不授权真实下单、自动调度或真实通知。

## 1. 当前产品实例

| 项目 | 值 |
|---|---|
| 产品数据库 | `E:\CODEX\Stock_selection\accumulation_breakout\runtime\lhb_product.db` |
| 原始生产库 | `E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db`（只读源，禁止迁移） |
| 默认访问地址 | `http://127.0.0.1:8123/v2/lhb/radar` |
| 数据范围 | 真实龙虎榜 20260824–20260828 |
| 产品模式 | `research_only=true`、`may_generate_orders=false` |
| 调度/实盘旗标 | 全部 `false` |

所有命令从仓库根目录执行：

```powershell
Set-Location E:\CODEX\Stock_selection\accumulation_breakout
```

## 2. 启动和停止产品

启动正式构建与只读 API：

```powershell
.\scripts\start_lhb_product.ps1
```

也可以显式指定副本和端口：

```powershell
.\scripts\start_lhb_product.ps1 `
  -DbPath E:\CODEX\Stock_selection\accumulation_breakout\runtime\lhb_product.db `
  -Port 8123
```

服务以前台进程运行，按 `Ctrl+C` 停止。启动器拒绝 `runtime\stock_data.db`，并强制：

- `LIVE_TRADING_ENABLED=false`
- `V2_PIT_READ_ENABLED=false`
- `DAILY_SCHEDULER_ENABLED=false`

## 3. 每日盘后运行

确认数据源已发布后，显式运行一个交易日：

```powershell
.\.venv312\Scripts\python.exe .\scripts\run_lhb_eod.py `
  --db E:\CODEX\Stock_selection\accumulation_breakout\runtime\lhb_product.db `
  --trade-date YYYYMMDD `
  --confirm-published
```

`--confirm-published` 表示操作者确认该交易日数据应已发布；只有此时零行响应才可判为 `VALID_EMPTY`。不确认时，零行保持 `NOT_PUBLISHED`，不得把抓取异常伪装成无榜单。

流水线顺序为：抓取 → 跨源状态记录 → 标准化 → 席位映射 → 画像 → 研究信号 → 日报 → dry-run 告警。成功退出码为 0；失败或上游阻断返回非 0/阻断状态，并保留 DAG attempt。

重要时间语义：

- 当天披露后及时运行，才允许形成当日 research observation，最早执行时间仍是下一交易日开盘。
- 历史晚到回填不会补造当时不可见的信号；因此本次 5 日历史 soak 的 signal 数为 0，是预期的防未来函数行为。
- 同一输入重跑按 manifest/content hash 和 DAG scope 审计，不覆盖历史修订。

## 4. 创建或刷新隔离副本

仅当需要从最新行情库创建一个新的产品副本时运行：

```powershell
.\.venv312\Scripts\python.exe .\scripts\prepare_lhb_product_db.py `
  --source E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db `
  --target E:\CODEX\Stock_selection\accumulation_breakout\runtime\lhb_product_next.db
```

约束：

- `source` 和 `target` 必须是绝对路径且不能相同。
- 源库以 SQLite read-only + consistent backup 打开。
- 迁移只在目标副本执行；目标若已存在则不会覆盖，只补 pending migration。
- 完成 schema/数据核验前，不要替换当前 `lhb_product.db`。

## 5. 日报、告警和质量检查

每日日报：

```text
runtime/lhb_reports/YYYYMMDD.json
```

重点字段：

- `source_status`：必须区分 `COMPLETE / DEGRADED / VALID_EMPTY / NOT_PUBLISHED / FETCH_FAILED`。
- `official_reconciliation`：当前应为 `NOT_AUTHORIZED`，不能伪装为已对账。
- `normalized`：事件、席位交易、排名和新增席位数量。
- `signals`：只有披露时点真实可见且通过硬否决的研究观察数。
- `research_only=true`、`may_generate_orders=false`：任何变化都应停止使用并复核。

当前告警仅写入 `lhb_alert_delivery`，且 `dry_run=1`。`CREATED` 不等于已发送，更不等于 ACK。

## 6. 验收命令

```powershell
.\scripts\run_lhb_pytest.ps1
.\.venv312\Scripts\python.exe .\scripts\check_architecture.py --strict
Push-Location .\web\frontend
npm.cmd run build
Pop-Location
```

浏览器端到端脚本为 `tests/playwright_lhb_product.py`；它针对正式 dist 验证六个龙虎榜页面，并把截图写到 `runtime/lhb_browser_e2e.png`。

## 7. 当前不能做的事

- 不得把龙虎榜标签描述为具体自然人的确定身份。
- 不得依据当前数据声称已发现稳定 edge 或确定性收益。
- 不得打开 `LIVE_TRADING_ENABLED`，也不得从本产品生成真实订单。
- 未完成官方跨源核验、shadow maturity 和单独调度验收前，不得打开每日自动调度。
- 未配置真实通知与 ACK 前，不得把 dry-run 告警描述为已送达。

最终状态与证据见 `docs/ACCEPTANCE-LHB-V1.md`。
