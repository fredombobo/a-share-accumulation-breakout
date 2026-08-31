# LHB-T04 Handoff — 事件标准化、期间解析和席位金额去重

> 自报完成不等于验收通过。

## 1. 身份

- 任务 ID：T04
- Agent 角色：实现 Agent（Wave B）
- 基线 commit：`f3075e96b565df9f8df3e4f681fc929dfedb3c77`（`closers-g2-split`）
- 交付 commit：无
- 时间（Asia/Shanghai）：2026-08-29

## 2. 范围核对

新增：

- `ab_screener/domain/lhb_normalization.py`
- `ab_screener/application/lhb_transform.py`
- `tests/test_lhb_normalization.py`
- `tests/fixtures/lhb/duplicate_cases.json`

契约微调（T01）：`LhbEventKey` 允许 `UNRESOLVED_WINDOW` 搭配已知 `reason_code`（累计窗缺日历时不猜日期，也不丢掉原因代码）。

未改 DAG、API、前端、feature flag、生产库。

## 3. 设计

- 原因规则版本 v1，更具体规则在前（三日累计不会落到 D1）。
- 缺日历的 D3/D10/D30 → `UNRESOLVED_WINDOW`，`period_start/end` 为空。
- 同窗同股多原因：多个 `lhb_event`，同一 `flow_fingerprint`，席位金额只挂主事件一次。
- 同席位买卖双榜：一条 trade，两条 rank。
- 指纹对席位排序敏感字段排序，输入行顺序变化不改变 fingerprint。
- 金额质量检查只出 OK/WARN/UNRESOLVED，不要求席位净额恒等于成交额。

## 4. TDD 证据

```powershell
.\.venv312\Scripts\python.exe -m pytest tests\test_lhb_normalization.py -q
# 见全量 LHB 40 passed
```

## 5. 质量证据

- Pytest：`test_lhb_normalization.py` 含在 40 passed
- Ruff / Mypy：pass
- 真实 Token / 生产库：未使用

## 6. 回滚

删除上述新增文件；若需还原 T01 的 `UNRESOLVED_WINDOW` 键约束，还原 `lhb_contracts.py` 中 `LhbEventKey.__post_init__`。

## 7. 自评

- 建议判定：待验收
- 未把标准化结果写入席位主数据（T05）或信号引擎（T08）
- 未宣称可实盘或保证收益

## 8. 管理者复验

- 最终判定：**返工复验通过**。
- 已关闭：Tushare 默认元口径与十亿元级量级回归通过；真实数字三日、北交所对称偏离、15% 价格涨跌和否定词已覆盖；非 A 股在事件标准化前过滤。
- 下一步：T06 可继续；完整证据见 `docs/ACCEPTANCE-LHB-T01-T05-2026-08-29.md`。
