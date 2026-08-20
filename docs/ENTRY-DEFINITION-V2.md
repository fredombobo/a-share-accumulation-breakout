# ENTRY 定义 v2（A_POOL_STRICT_NEXT_OPEN_V2）

> 状态：登记只读（非生产候选）。生产候选默认仍为 `A_POOL_STRICT_NEXT_OPEN_V1`；
> 仅独立研究通过七闸门后才允许通过 `ACTIVE_ENTRY_DEFINITION_ID` 切换。
> 权威代码：`ab_screener/domain/entry_definition_v2.py` + `entry_registry.py`。

## 与 V1 的关系

- 入场时序完全一致：突破日**下一交易日开盘**（无 open 用 close）。
- V2 = V1 + 信号判定增量（2026-08-16 突破逻辑 v2 的契约化），不改变 V1 历史结果
  （`tests/test_entry_definition_v1_golden.py` 锁定 V1 golden）。

## V2 语义增量

| 维度 | V1 | V2 |
|---|---|---|
| 箱体搜索 | 先找箱体（右端可延伸入突破窗口） | **两步式**：先找突破日候选，再在其前一根之前搜箱体（箱体不含突破日） |
| 位置护栏 | 基于观察窗尾部切片（长箱时失效） | 基于完整窗口 + 箱前历史不足 fail-closed |
| 突破后站稳 | 仅最新收盘 > 箱顶 | 突破后跌破箱体上沿次数 strict=0 / relaxed=1，且最新仍在上方 |
| 长期趋势 | MA5/MA20 多头 | 增加 strict 须站上 **MA60**（过滤底部震荡假突破） |
| 容差 | — | MA60 跌破上沿容差 0.5% |

## 注册表

- ID：`A_POOL_STRICT_NEXT_OPEN_V2`；semantic hash 由 `entry_registry.semantic_hash` 计算
- 未知定义 fail-closed；报告须引用 `entry_definition_id + entry_semantic_hash`
- 切换生产候选：`ACTIVE_ENTRY_DEFINITION_ID=A_POOL_STRICT_NEXT_OPEN_V2`（环境变量，需门禁支持）
