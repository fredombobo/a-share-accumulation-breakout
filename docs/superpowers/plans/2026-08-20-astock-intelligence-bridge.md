# Astock × accumulation_breakout 情报桥 v1 — 实施计划

> **给实现 Agent 的入口索引见：**  
> [2026-08-20-astock-bridge-index.md](2026-08-20-astock-bridge-index.md)

| 字段 | 内容 |
|------|------|
| 文档 ID | `ASTOCK-INTELLIGENCE-BRIDGE-V1-PLAN` |
| 日期 | 2026-08-20 |
| 宿主仓库 | `E:\CODEX\Stock_selection\accumulation_breakout` |
| 旁路仓 | `E:\CODEX\Stock_selection\astock`（https://github.com/fredombobo/astock ，MIT） |
| 状态 | **待实现**（验收由独立检查 Agent 执行，实现 Agent 不得自称 ACCEPTED） |
| 写路径 | **禁止**改扫描结果、纸面账本、信号状态、研究晋级 |

---

## 1. 产品决策（不得推翻）

1. **不合仓、不双主。** AB 仍是扫描 / 研究 / 纸面的唯一写路径。
2. 从 astock **只摘取只读情报**：市场宽度增强、涨停梯队、七指数、可选 HTTP 全球行情。
3. **禁止移植**：PE/PB 六套选股、float 持仓表、MA 金叉回测、LLM「买入/目标价」进 A 池或出单。
4. astock 进程 **可选**。未启动时本地 SQLite 仍能给出宽度/涨停/指数；HTTP 失败必须降级，不得 500。
5. 输出必须带 `side_effects=false` 与 `not_a_pool=true`。

许可：MIT，可复制算法与口径，须保留版权声明（新文件头注释引用 astock LICENSE）。

---

## 2. 参考实现（旁路仓，只读抄口径）

| astock 文件 | 可借鉴 | 不可照搬 |
|-------------|--------|----------|
| `backend/data/market.py` `get_market_breadth` / 涨停板宽 | 10%/20% 板、涨跌家数 | 写缓存、启动 auto-sync |
| `get_limit_up_pool` | 梯队字段 | 实时新浪无 as_of |
| `get_a_index_realtime` | 七指数名单 | 硬编码假 0 当成功 |
| `get_global_markets` | HTTP 路径 `/api/market/global` | 无超时、无降级 |
| `backend/services/engine.py` 六套 PE 选股 | **不移植** | — |
| `backend/ai/prompts.py` 强制买卖建议 | **不进 A 池** | AB 已有独立 AI 解读 |

AB 已有：`ab_screener/intelligence/breadth.py`（涨跌家数）、`/api/v2/intelligence/*`、`/api/v2/desk`、`pages/v2/Desk.tsx`、`pages/v2/Intelligence.tsx`。本计划是 **增量**，不是重写情报模块。

工作区可能已有未完成草稿（实现 Agent 应核对后补齐或重写，不得留下半截无测试代码）：

- `ab_screener/intelligence/limit_up.py`
- `ab_screener/intelligence/indices.py`
- `ab_screener/intelligence/desk_supplement.py`
- `ab_screener/integrations/astock_client.py`

---

## 3. 架构约束

- 依赖方向：`api → intelligence/integrations → sqlite 只读`。
- `ab_screener/api/**` 与 `web/backend_app.py` **不得** `import sqlite3` / `subprocess`（`scripts/check_architecture.py`）。
- SQLite：只读 URI `file:{path}?mode=ro`；禁止 `INSERT OR REPLACE`。
- 新代码不得改 `paper_trading/`、`signals.py`、`run_screener.py`、订单路由。
- `LIVE_TRADING_ENABLED=true` 仍须使平台配置失败。
- 测试禁止真实 Token、禁止必连外网。HTTP 用 `urllib.request.urlopen` mock。

---

## 4. 数据口径

### 4.1 交易日

- 查询参数 `trade_date` 为 `YYYYMMDD`。
- 缺省：`SELECT MAX(trade_date) FROM daily`；库空 → 整包 `status=INSUFFICIENT`，`reason=no_trade_date`。

### 4.2 涨跌幅

`pct = (close / pre_close - 1) * 100`，`pre_close<=0` 跳过。

### 4.3 板宽（对齐 astock）

| 代码前缀 | 板 |
|----------|----|
| `300*` / `301*`（.SZ） | 20% |
| `688*`（.SH） | 20% |
| 其余 A 股 | 10% |

涨停：`pct >= limit - 0.1`；跌停：`pct <= -(limit - 0.1)`。

### 4.4 七指数（须在 `daily` 中按 ts_code 存在才展示）

`000001.SH` 上证、`399001.SZ` 深成、`399006.SZ` 创业板、`000688.SH` 科创50、`000300.SH` 沪深300、`000905.SH` 中证500、`000852.SH` 中证1000。  
一条都没有 → `indices.status=INSUFFICIENT`，`items=[]`，**禁止**用 close=0 填满七行。

### 4.5 HTTP（可选）

环境变量 `ASTOCK_BASE_URL`（例 `http://127.0.0.1:8900`），未设置 = 关闭。  
超时 **2s**。探测：

- `GET {base}/health`
- `GET {base}/api/market/global`

失败：`astock.reachable=false` + `error` 短字符串；HTTP 层仍 200。

---

## 5. 模块与文件（实现清单）

| 文件 | 职责 |
|------|------|
| `ab_screener/intelligence/limit_up.py` | `board_limit_pct`、`limit_up_ladder` |
| `ab_screener/intelligence/indices.py` | `A_SHARE_INDICES`、`index_snapshot` |
| `ab_screener/intelligence/desk_supplement.py` | 组装包；`latest_trade_date` |
| `ab_screener/integrations/astock_client.py` | `probe_astock`、`fetch_json` |
| `ab_screener/api/routers/intelligence.py` | 新增 GET，**不**在 router 里写 SQL |
| `tests/test_limit_up_ladder.py` | 板宽 + 空日 INSUFFICIENT |
| `tests/test_index_snapshot.py` | 缺指数 INSUFFICIENT；有则 PASS |
| `tests/test_astock_client.py` | 无 URL / 超时 / mock 200 |
| `tests/test_desk_supplement.py` | 组装 + 闸门字段 + 不写库 |
| `tests/test_intelligence_supplement_api.py` | TestClient GET |
| `web/frontend/src/types/intelligence.ts` | 类型 |
| `web/frontend/src/api/intelligence.ts` | `fetchDeskSupplement` |
| `web/frontend/src/pages/v2/Intelligence.tsx` | 涨停 + 指数块 |
| `web/frontend/src/pages/v2/Desk.tsx` | 可选摘要条 |
| `tests/test_openapi_contract_v2.py` | `REQUIRED_V2_PATHS` 增加新 path |
| `docs/handoffs/ASTOCK-BRIDGE-V1.md` | 实现完成后填写 |

增强 `breadth.py` **允许**增加 `limit_up`/`limit_down` 计数字段，但 **不得**改变现有 `advances/declines/unchanged/total` 语义（`tests/test_market_breadth.py` 必须仍绿）。

---

## 6. JSON 合同

### 6.1 `GET /api/v2/intelligence/desk-supplement?trade_date=`

```json
{
  "side_effects": false,
  "not_a_pool": true,
  "trade_date": "20260810",
  "status": "PASS",
  "reason": null,
  "breadth": {},
  "limit_up": {
    "trade_date": "20260810",
    "status": "PASS",
    "reason": null,
    "limit_up": 2,
    "limit_down": 1,
    "items": [{"ts_code": "000001.SZ", "pct_chg": 10.0, "board_limit_pct": 10.0}]
  },
  "indices": {
    "trade_date": "20260810",
    "status": "PASS",
    "reason": null,
    "items": [{"ts_code": "000300.SH", "name": "沪深300", "close": 4000.0, "pct_chg": 1.2}],
    "coverage": 0.1429
  },
  "astock": {
    "enabled": false,
    "reachable": false,
    "base_url": "",
    "global": null,
    "error": null
  },
  "disclaimer": "研究情报，不是买卖指令，不进入 A 池。"
}
```

`status`：本地 `breadth.total>0` 为 `PASS`，否则 `INSUFFICIENT`。  
`limit_up.items` 按 `pct_chg` 降序，最多 20。

### 6.2 可选拆分 GET（若实现则必须同样只读）

- `GET /api/v2/intelligence/limit-up?trade_date=&top_n=20`
- `GET /api/v2/intelligence/indices?trade_date=`

至少 **desk-supplement 为必须**。

---

## 7. 实现步骤（TDD）

1. 先写 `tests/test_limit_up_ladder.py`（含 300xxx 20% 板、空日 INSUFFICIENT）。
2. 实现 `limit_up.py` 至测试绿。
3. `test_index_snapshot.py` → `indices.py`。
4. `test_astock_client.py`（无 URL；mock urlopen 成功；mock 抛错不 raise）。
5. `test_desk_supplement.py`：断言 G1（函数前后 `SELECT COUNT` 不变）、G8 字段。
6. 在 `intelligence.py` router 增加 GET；`test_intelligence_supplement_api.py` + OpenAPI 集合。
7. 前端类型 + Intelligence/Desk 展示；`npx tsc --noEmit`（`web/frontend`）。
8. 跑验收文档中的命令，填写 handoff，**不要**改 `docs/STATUS.md` 除非用户要求。

---

## 8. 回滚

删除本计划列出的新文件与 router 增量即可。无迁移、无新业务表。草稿文件若未完成验收，可整文件删除。
