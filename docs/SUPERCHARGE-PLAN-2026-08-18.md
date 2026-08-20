# SUPERCHARGE-PLAN — 系统深度增强执行计划（2026-08-18）

> 定位：个人学习研究平台（非商业）；基础设施（SQLite 单机）保持不变。
> 目标：数据完整化 + 风险模型深度 + 因子库体系，全部 TDD、离线门禁全绿、诚实 INSUFFICIENT。

## A. PIT 数据完整化（进行中）

| 数据集 | 状态 | 备注 |
|---|---|---|
| daily_history | ✅ 976/976 天（518 万行） | 已完成 |
| daily_basic_history | ✅ 976/976 | 已完成 |
| adj_factor_history | ✅ 976/976（523 万行） | **用户指定必须完成——已达成** |
| moneyflow_history | ✅ 976/976（495 万行） | 已完成 |
| fina_indicator_history | 🔄 回填中 | **按 ts_code 分区**（镜像网关不支持纯 period 查询，实测确认） |
| stock_basic_history | 🔄 回填中 | ALL 单分区（5546 只） |
| top_list_history | 🔄 回填中 | 976 交易日分区（龙虎榜，实测可用） |
| margin_history | 🔄 回填中 | 976 交易日分区（两融 T+1 披露，最后一日可能为空——合法） |
| cyq_history | 🔄 回填中 | 976 交易日分区（筹码分布，实测可用） |
| holder_history | 🔄 回填中 | 按 ts_code 分区（上市+退市 5541+） |

出口：全部数据集 100% 覆盖 + 抽样 hash 核对 → 判定 `V2_PIT_READ_ENABLED`。

### A 阶段修复记录（2026-08-18 追加）
- `v2:pit_history` checksum 漂移根因：还原时新增注释行导致 `apply` 函数 `co_firstlineno` 偏移 → 已移除恢复（5d679ee799f7e4b3），运行库 11/11 迁移兼容。
- 数据集短名统一：`ALL_DATASETS`（表名 = `{ds}_history`）；checkpoint/CLI/coverage 此前表名/短名混用导致 `--coverage` 全 0。
- `holder`/`fina_indicator` 分区键 = `stock_basic`+`delisted_basic` 的 ts_code（缺表明确报错，不静默空跑）。

## B. 新数据集接入（Tushare 权限实测 2026-08-18）

### 可用（个人 Token）
| 数据集 | 接口 | 粒度 | 用途 |
|---|---|---|---|
| 龙虎榜 | `top_list` + `top_inst` | 每日 | 游资/机构行为、异常交易识别 |
| 两融 | `margin_detail` | 每日个股 | 杠杆情绪、融资盘压力 |
| 股东行为 | `top10_holders` / `holder_number` | 报告期 | 筹码集中度、大股东变化 |
| 筹码分布 | `cyq_perf` | 每日个股 | 获利盘/套牢盘、成本分布 |

### 不可用（权限不足，诚实记录，不伪造）
- 公告全文 `anns_d`、新闻舆情 `major_news`、一致预期 `report_rc`、同花顺概念 `ths_index`
- 替代：板块映射用 `stock_basic.industry`（已有）+ `index_member`（指数成分）

设计：按 PIT 模式扩展（新历史表 + 适配器 + backfill 分区 + 数据状态聚合）。

## C. 风险模型深度（纯领域，TDD）✅ 已完成（ac3301f）

1. `risk/covariance.py`：样本协方差 + 收缩估计（Ledoit-Wolf）✅
2. `risk/monte_carlo.py`：蒙特卡洛 VaR/CVaR（确定性种子）✅
3. `risk/multi_factor.py`：多因子风险模型（因子暴露 → 因子协方差 → 特质风险）✅
4. `risk/stress_library.py`：极端压力情景库（2008/2015/2020 参数化，非历史回放伪造）✅
5. `tests/test_risk_model_depth.py`：7 用例全绿 ✅

## D. 因子库 + alpha 衰减 + 组合优化 ✅ 已完成（57a5c51）

1. `factors/registry.py`：因子注册表（动量/反转/波动/价值/质量/资金流/量价）✅
2. `factors/alpha_decay.py`：IC 序列 + 半衰期 + 衰减监控 ✅
3. `factors/portfolio_optimization.py`：均值-方差（风险厌恶参数化）+ 风险平价 ✅
4. 因子与六形态信号正交性检查（不重复暴露）✅
5. `tests/test_factor_system.py`：5 用例全绿 ✅

## 执行顺序

```
A（后台已跑）→ B（本轮：迁移+适配器+回填启动）→ C（TDD）→ D（TDD）→ 门禁+基线
```

## 诚实约束

- 权限不足的数据集记录 INSUFFICIENT，绝不模拟/伪造
- 蒙特卡洛/压力情景为参数化模型，非历史精确回放；在文档注明假设
- 因子 IC 需真实数据积累，初期可能 INSUFFICIENT（样本不足）——正确
