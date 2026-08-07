# 阶段2 验收报告：账户初始化与旧持仓导入

日期：2026-08-07
状态：✅ 全部通过

## 交付内容（`paper_trading/account.py` + 后端 API）

### 1. 账户初始化
- `create_account(db, initial_cash_fen)`：创建唯一账户（account_id=1）+ INITIAL 现金流水（running balance）
- 重复创建 → `ACCOUNT_ALREADY_EXISTS`（409）；未创建读取 → `ACCOUNT_NOT_FOUND`
- `get_account()` / `account_exists()` / `opening_equity()`（期初权益 = 现金 + 持仓收盘市值）

### 2. portfolio.json 导入（预览-确认）
- `parse_portfolio_json`：读取 positions（文件缺失/解析错误 → 领域错误）
- `validate_import_item`：逐条校验——未知代码 / 非整数数量 / 负数 / 缺失成本 / 缺失数量，**逐条列出错误不静默修正**
- `preview_import`：校验 + 当前行情（最近收盘价/日期）+ valid/invalid 计数 + 源文件哈希
- `commit_import`：确认导入——
  - 每条有效持仓生成 OPENING 订单（FILLED）+ OPENING fill（模型版本 OPENING_IMPORT）+ pt_position_lot 批次
  - **不倒扣初始化现金**
  - 已持有>1日（opened_at < as_of_date）→ 立即卖；当日建仓 → T+1 下一开市日
  - 幂等：按源文件 SHA-256 哈希检查，同哈希已导入 → `skipped_existing` 不重复加仓
  - 原 portfolio.json 保持只读（回滚依据）

### 3. 后端 API（web/backend_app.py）
- `GET/POST /api/paper/account`、`GET /api/paper/dashboard`、`POST /api/paper/import/preview|commit`、`GET /api/paper/gates/status`
- 错误统一 `{code, message, details, retryable}`（HTTPException detail），409/429/500
- dashboard 明确标识「纸面仿真，不会向券商下单」

## 验收结果

| 验收项 | 判定规则 | 实测 | 结果 |
|---|---|---|---|
| 有效持仓成本/份额/备注完整迁移 | 导入后 lot 行含 cost/shares | 2 条批次正确 | ✅ |
| 非整数/负数/未知代码/缺失成本逐条显示 | 不静默修正 | 4 类错误均列出 | ✅ |
| 同文件重复导入资产不变化 | 同哈希 → skipped | 持仓数不变 | ✅ |
| 期初权益 = 现金 + 持仓收盘市值 | 公式验证 | 1,000,000 + 255,000 | ✅ |
| 期初仓立即可卖 / 当日仓 T+1 | sellable_date 规则 | 8/6 vs 8/10 | ✅ |
| 账户创建 + 重复冲突 | 唯一账户 | 409 ACCOUNT_ALREADY_EXISTS | ✅ |
| 不倒扣初始现金 | 导入后 cash_fen 不变 | 1,000,000 不变 | ✅ |

**测试**：`tests/test_account_import.py` 8 项全部通过。

## 文件清单
- **新增**：`paper_trading/account.py`、`tests/test_account_import.py`
- **修改**：`web/backend_app.py`（6 个 paper API 路由 + 结构化错误处理）

## 备注
- 期初仓的 order/fill 使用 OPENING 前缀 ID + `fill_model_version='OPENING_IMPORT'`，与真实成交可区分。
- 幂等基于源文件哈希（pt_audit_event.action='PORTFOLIO_IMPORT' 记录），文件内容变更新哈希 → 可再次导入（增量）。
- 真实库已创建测试账户（现金 10000 元），后续阶段可直接复用；如需重置可手动删除 pt_account/pt_position_lot 行。
