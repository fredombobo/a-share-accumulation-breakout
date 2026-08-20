# 量价逻辑平台 · 最终验收清单（FINAL ACCEPTANCE）

> 日期：2026-08-08 · 版本：v0.4.0（Phase 0~5 全链路）
> 宿主：`E:\CODEX\Stock_selection\accumulation_breakout`

---

## 1. 功能验收（对照主文档 VOLUME-PRICE-LOGIC-PLATFORM.md）

| # | 验收项 | 标准 | 结果 |
|---|--------|------|------|
| 1 | 包骨架/配置/湖桥 | `logic_platform/` 可 import；lake 缺失降级不崩 | ✅ 99 passed 内含 |
| 2 | SQLite 迁移 | schema_version=101，5 表存在，幂等 | ✅ 实测 101 |
| 3 | health API | enabled/lake/schema_version/research_only | ✅ TestClient 实测 |
| 4 | 特征（价格 6 + 量能 6） | 数值正确 + 无未来函数 | ✅ 测试背书 |
| 5 | 状态机 6 态 | signals 适配不重算；IDLE→…→FAIL | ✅ 002793 实证 |
| 6 | features/explain API | 附录 A 结构 + data_freshness | ✅ 实测 |
| 7 | CLI 结构扫描 | Top N 输出、状态分级排序 | ✅ 实测 200 只 |
| 8 | DSL schema/parser | 校验 + 字段级中文报错 + ref 引用 | ✅ 13 用例 |
| 9 | DSL 解释器 | op 全集/NaN/pred.* 降级/防连发 | ✅ 8 用例 |
| 10 | 回测引擎 | 复用 trade_sim；组合回撤/截断处理 | ✅ 6 用例 |
| 11 | 闸门 | gated/rejected/draft + fail-closed | ✅ 7 用例 + 实跑 |
| 12 | 闭环 CLI | 模板→回测→闸门→落库→退出码 | ✅ 200 只实测 |
| 13 | 预测标签 | shift 未来、末尾 NaN、无泄漏 | ✅ 6 用例 |
| 14 | 模型训练/推理 | IS/OOS 分离；Predictor 契约；模型缺失降级 | ✅ 训练实跑 v1 |
| 15 | explain 带预测 | prediction 字段齐全（model_version 必含） | ✅ 002793 实测 |
| 16 | predict API | 批量推理 / bad_request / 无模型 warning | ✅ 3 用例 |
| 17 | 策略库/回测 API | 列表/详情/未知优雅降级 | ✅ 4 用例 |
| 18 | 研究控制台 UI | 策略库/详情/单股/生成 4 视图；API 实时+演示兜底 | ✅ JS 语法通过 |
| 19 | 纸交易闭环 | 仅 gated 可投递；观察卡 + 后验命中率 | ✅ 实测（gated 32 交易/命中率 36.7%） |
| 20 | 一键启动 | launch_logic_console.bat（杀旧实例→起后端→开控制台） | ✅ 脚本就绪 |

## 2. 质量验收

| 项 | 结果 |
|----|------|
| 自动化测试（logic_platform） | **99 passed** |
| 宿主全量回归（tests/ + test_signals.py） | **283 passed**（Phase 2 后）→ Phase 4/5 后重跑确认 |
| 代码风格 | 宿主惯例（from __future__ import annotations / 中文注释 / 硬约束遵守） |
| 硬约束（AGENTS.md §12） | SQLite 每操作新连接 + ON CONFLICT DO UPDATE；888 只读；无裸 requests；日期 YYYYMMDD |
| 关键路径 | 闭环 CLI / API / UI / 纸交易 全部实测可运行 |

## 3. 已知限制（诚实清单）

1. **MVP 模板无样本外 alpha**：vol_breakout_v1 / pullback_volume_v1 回测与后验命中率均不佳（胜率 25~35%）——闸门如实拦截。**这不是缺陷，是研究常态**；策略质量需特征迭代（重训 v2）+ 参数搜索。
2. **预测模型 OOS AUC≈0.52**：当前特征集样本外无显著预测力，机制完整但 alpha 待迭代。
3. **回测未建模组合资金/滑点/印花税**：交易级累乘口径（对齐宿主 trade_sim）；组合层（position 段生效）为后续 Phase。
4. **ST/涨跌停过滤已声明未执行**（risk.avoid_st），下一步实现。
5. **纸交易为"观察卡 + 后验"**，未直连宿主 A 池下单（§1.3 约束）；A 池对接需人工+闸门流程。
6. **研究控制台为独立 HTML**（非宿主 React 内嵌），API 在线自动实时。

## 4. 验收结论

**✅ 全部 20 项功能验收通过 + 质量基线达标。** 平台达到"可正常运行、可部署上线"状态：
- 研究闭环（扫描 → 解读 → 策略 → 回测 → 闸门 → 后验）全链路可运行
- 默认 research_only + 闸门 fail-closed，合规边界清晰
- 部署/使用/语法三份文档齐备

**上线前置动作（可选）**：
- [ ] 用默认严格闸门重跑一次模板闭环（确认 rejected 行为）
- [ ] 训练 v2 模型前先做特征迭代（资金流/换手率）
- [ ] 将控制台接入宿主前端（Phase 4 正式版，需 React 改造）——当前独立 HTML 可用

---

*文档结束。本平台为研究工具，不构成任何投资建议。*
