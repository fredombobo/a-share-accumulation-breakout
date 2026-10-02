# 研究预登记模板 v0（Phase 2 起）

> 用法：任何新假设在跑第一次回测/事件研究**之前**复制本模板，替换尖括号内容，
> 计算 SHA-256 后写入 `runtime/research/<hypothesis_id>/registration.json`。
> 没有预登记的探索只允许出现在 `exploratory/`，不得晋级、不得写入门禁报告。

## 1. 身份

| 字段 | 值 |
|---|---|
| hypothesis_id | `<H-YYYYMMDD-slug>` |
| version | `<v1>` |
| 预登记时间 | `<ISO8601>` |
| 作者 | `<name>` |
| 代码版本 | `<git sha + build_version>` |
| 数据版本 | `<dataset_version / db fingerprint>` |
| 预登记 SHA-256 | `<self hash>` |

## 2. 机制（必须先有，再谈数据）

- 一句话：`<什么摩擦/行为偏差让价格偏离，谁在何时被迫交易>`
- 反方解释：`<为什么可能是噪声/已被套利掉>`
- 与已有失败假设的区别：`<不能只是阈值不同>`

## 3. 冻结定义

| 项 | 值 |
|---|---|
| 股票池 | `<全市场 / 指数成分 / 板块；生存者偏差如何处理>` |
| 样本区间 | IS `<YYYYMMDD–YYYYMMDD>`；OOS `<...>`（OOS 开发期不可见） |
| 信号定义 | `<公式 + 冻结代码文件 + 哈希>` |
| 入场 / 出场 | `<次日开盘 / 持有期 / 止损止盈>` |
| 成本模型 | `<佣金/印花税/滑点/冲击函数版本>` |
| 基准 | `<随机基线 / MA20-60 / 沪深300 / 行业中性>` |
| 参数网格 | `<离散候选；禁止连续调参>` |
| 试验上限 | `<≤5>` |
| 随机种子 | `<int>` |

## 4. 评估门禁（引用 STRATEGY-SCORECARD-V0 的 G0–G8）

- G2 条件信息：事件日聚类 95% CI 下界 > 0，且优于伪事件；
- G3 OOS：≥2 年、未在开发期查看；
- G4：PBO < 10%、DSR ≥ 0.95、MinTRL ≥ 1、参数 ±20% 不翻转、去掉最好 5% 仍正；
- G5：2× 成本为正、3× 不破、ADV 参与率上限与容量；
- G6：shadow ≥6 个月、滑点偏差 <30%；
- G8：波动目标 / 行业市值中性 / 回撤预算。

## 5. 止损（任一命中即终止并归档，禁止微调重试）

1. `<如：OOS 净 Sharpe < 0.3>`
2. `<如：PBO > 20%>`
3. `<如：2× 成本期望 ≤ 0>`
4. `<如：前瞻滑点偏差 > 30%>`

## 6. 结果与产物

- 结果目录：`runtime/research/<hypothesis_id>/`
- 必须包含：`registration.json`、`events/positions`、`metrics.json`、
  `cost_stress.json`、`report.md`、全部输入 SHA-256。
- 结论只能三选一：`PASS_CANDIDATE` / `FAIL` / `INSUFFICIENT`；
  `PASS_CANDIDATE` 也只进入 shadow，不自动改每日选股。
