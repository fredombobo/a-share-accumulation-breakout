# 预登记：H-20260925-abnormal-turnover（注意力 / 情绪溢价）

> 状态：**已冻结** 2026-09-25T20:45:00+08:00 ｜ 通用协议 `docs/prereg/COMMON-CROSS-SECTION-PROTOCOL-V1.md` SHA-256 `7706a0d08cf31014b79aa1c6ca1144899aea566bab992e64ed25de7b6a6778b8`
> 口径：ADR-022；门禁：STRATEGY-SCORECARD-V0。本文件冻结后不得修改。

## 1. 机制

- 一句话：换手突然放大反映散户注意力与情绪涌入，推高价格超出基本面，随后回吐（Liu-Stambaugh-Yuan 2019 以异常换手构造 A 股情绪因子）。
- 反方解释：换手放大也可能来自真实信息到达（此时后续应为正漂移）；在牛市末期效应可能反转。
- 与已有失败假设的区别：已有突破策略把放量当作**正面**信号；本假设方向相反且为横截面排序，不复用任何突破条件。

## 2. 信号（冻结）

- `ATURN` = 形成日前 20 个交易日平均 `turnover_rate` ÷ 前 250 个交易日平均 `turnover_rate`；任一窗口有效天数不足 80% 则缺失。
- 最优五分位 = **ATURN 最低**的五分位。
- G4 扰动：短窗 16/24 日（长窗固定 250 日）。

## 3. 止损（除标尺止损外）

- IS 的 D 均值 ≤ 0 → 立即 FAIL，不解封 OOS。

## 4. 试验

`max_trials = 3`；结果目录 `runtime/research/scorecard/H-20260925-abnormal-turnover/`。
