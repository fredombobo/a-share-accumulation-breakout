# Astock 情报桥 v1 — 验收矩阵

| 字段 | 内容 |
|------|------|
| 文档 ID | `ASTOCK-INTELLIGENCE-BRIDGE-V1-ACCEPTANCE` |
| 计划 | [2026-08-20-astock-intelligence-bridge.md](2026-08-20-astock-intelligence-bridge.md) |
| 验收者 | **独立检查 Agent**（不得由实现 Agent 自签 ACCEPTED） |
| 总结论枚举 | `ACCEPTED` / `REJECTED` / `BLOCKED` |

检查 Agent 必须 **亲自重跑** 下列命令并记录输出摘要与退出码，不得只读 handoff。

---

## A. 硬闸门（任一 FAIL → 总评不得 ACCEPTED）

| ID | 标准 | 验证方法 | PASS 条件 |
|----|------|----------|-----------|
| G1 | GET 只读 | `test_desk_supplement` 在调用前后对 `daily`/`scan_*`/`pt_*` 做 COUNT（表不存在则跳过该表） | 行数不变 |
| G2 | 不进 A 池 | grep 实现：`desk_supplement`/`limit_up`/`astock_client` 不得 import `run_screener`、`paper_trading`、`signal_pipeline` | 无匹配 |
| G3 | 空数据 INSUFFICIENT | 空库或错误 `trade_date` 的 ladder/indices/supplement | `status=INSUFFICIENT` 且指数 `items=[]` |
| G4 | HTTP 降级 | mock `urlopen` 抛错；未设 `ASTOCK_BASE_URL` | API 仍 200；`astock.reachable=false` |
| G5 | 架构 | `python scripts/check_architecture.py --strict` | 退出码 0 |
| G6 | 无实盘 | `LIVE_TRADING_ENABLED` 默认 false；`load_resolved_config(env={LIVE_TRADING_ENABLED:true})` 仍失败 | 既有 `test_architecture_boundaries` 绿 |
| G7 | 离线 | 新测试不得真实访问 sina/tushare；无 `.env` Token 读取作为成功条件 | 审查测试源码 |
| G8 | 契约字段 | supplement JSON | `side_effects is False` 且 `not_a_pool is True` 且含 disclaimer |

---

## B. 功能验收

| ID | 标准 | 验证 |
|----|------|------|
| F1 | 主板涨停 | fixture：`000001.SZ` close/pre_close 使 pct≥9.9 → 计入 `limit_up` |
| F2 | 创业板 20% | `300001.SZ` pct=10 **不是**涨停；pct≥19.9 才是 |
| F3 | 梯队排序 | items 按 `pct_chg` 降序，长度 ≤20 |
| F4 | 七指数 | 仅返回 `daily` 中存在的指数；缺则 INSUFFICIENT 而非假 0 |
| F5 | 宽度回归 | `pytest tests/test_market_breadth.py` 仍通过 |
| F6 | OpenAPI | `GET /api/v2/intelligence/desk-supplement` 出现在 `test_openapi_contract_v2.py` 的 REQUIRED 集合 |
| F7 | UI | Intelligence 或 Desk 展示宽度/涨停/指数之一；文案含非买卖/非 A 池 |
| F8 | 默认无 HTTP | 不设 `ASTOCK_BASE_URL` 时 `astock.enabled is False` |

---

## C. 必须执行的命令（检查 Agent 原样跑）

在仓库根 `E:\CODEX\Stock_selection\accumulation_breakout`：

```powershell
$py = "C:\Python314\python.exe"
# 若存在权威环境则优先：
# $py = ".\.venv312\Scripts\python.exe"

& $py -m pytest tests/test_limit_up_ladder.py tests/test_index_snapshot.py tests/test_astock_client.py tests/test_desk_supplement.py tests/test_intelligence_supplement_api.py tests/test_market_breadth.py tests/test_architecture_boundaries.py tests/test_openapi_contract_v2.py tests/test_v2_routers_smoke.py -q --tb=short

& $py scripts/check_architecture.py --strict

cd web\frontend
npx tsc --noEmit
```

可选（不阻塞 ACCEPTED，记录即可）：

```powershell
& $py -m pytest tests/test_entry_definition_v1_golden.py tests/test_platform_config.py -q
```

---

## D. 禁止项（出现即 REJECTED）

- 调用 astock `run_screener` / PE 预设作为 AB 扫描结果
- LLM 输出写入 `scan_run_candidates` 或订单
- 合并 astock SQLite 到 `runtime/stock_data.db`
- 新表使用 `INSERT OR REPLACE`
- 实现 Agent 在 STATUS 中写「已验收通过」
- 测试依赖外网成功

---

## E. 验收记录模板（检查 Agent 填写）

复制为 `docs/ACCEPTANCE-ASTOCK-BRIDGE-V1-YYYY-MM-DD.md`：

```markdown
# Astock 情报桥 v1 验收

- 日期:
- 检查 Agent:
- 实现 commit:
- 命令退出码: pytest=  architecture=  tsc=
- 闸门表: G1–G8 / F1–F8 逐项 PASS/FAIL + 证据
- 总评: ACCEPTED | REJECTED | BLOCKED
- 阻断项:
```

**ACCEPTED 条件：** G1–G8 全 PASS **且** F1–F8 全 PASS **且** 上列三命令退出码为 0。  
缺 UI 但 API+测试全绿 → 最多 `BLOCKED`（写明缺 F7），不得 ACCEPTED。
