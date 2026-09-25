# 评分 2 → 9 路线图（2026-09-25）

> 标尺：`docs/STRATEGY-SCORECARD-V0.md`。本文件回答“怎样把分数从 2 提到 9”，
> 并把可以做的工程与**只能靠时间和真实收益证据**的部分分开写清。

## 0. 先说结论

- 分数衡量的是**有没有可复算的真实 edge**，不是系统完整度。工程能把“测量”做对、
  把“伪造和自欺”堵死，但**不能制造 edge**。
- 现状 2 分的原因不是工程差，而是：形态层 G2 已测为无条件信息（3,244 事件，
  H20 CI 跨 0）、权威研究 FAIL（PBO 31%）、2× 成本为负。
- 按标尺，**9 分最早也要在一个机制过 6 分之后再积累 ≥12 个月前瞻记录**。
  即便一切顺利，现实日历上 9 分不早于 2028 年；任何更快的“达到 9 分”都只能是改口径。

## 1. 分数现在由命令裁决

```powershell
.\.venv312\Scripts\python.exe scripts\strategy_scorecard.py
```

- 实现：`ab_screener/research/scorecard.py`；证据根 `runtime/research/scorecard/`（运行证据，不入库）。
- 每个门禁证据必须列出工件路径与 SHA-256，逐个复核；假设级证据必须晚于预登记冻结时间；
  预登记文档冻结后被修改即判 INVALID；试验次数超过登记上限即 KILLED。
- 阈值与止损逐条取自标尺，代码里不放宽；`tests/test_strategy_scorecard.py` 覆盖每一档与每条止损。
- 事件研究产物可直接转为 G2 证据：`--import-g2 <输出目录> --hypothesis <ID>`。
- 文档里的“当前自评”从此以命令输出为准。

## 2. 分档路线

### 2 → 4：G0 数据与测量（工程为主，数周）

| 条件 | 现状 | 要做的事 |
|---|---|---|
| 全历史 PIT 可用时点 | 近年有；历史批量入库；筹码从 2026-09-25 起前向捕获 | **需要你的决定**（见 §3-1） |
| 退市/停牌/涨跌停/复权 | 执行模型 v2.1.3 已含一字板/停牌退出；退市名单已同步 | 核对退市股历史行情覆盖率，出审计报告 |
| 成本与冲击模型校准 | 有成本配置，无校准证据 | 按券商费率 + 冲击函数出校准报告（无真实成交时写明依据） |
| manifest 可复算 | 已有 dataset_partitions 指纹 | 出复算报告 |

产物：`system/G0.json` + 每项的审计报告工件。全部为 true 才得 4 分。

### 4 → 6：一个预登记机制通过 G1–G4（研究为主，结果不确定）

- **突破形态家族已经在 G2 失败**，在它上面换阈值、加过滤属于标尺禁止的“对失败假设调阈值重跑”。
  要到 6 分，必须换**新的经济机制**。
- G3 要求 ≥2 年、开发期从未查看的 OOS。**已被看过的数据不能再当 OOS**：
  - 筹码维度：2022-08~2026-08 已做过探索，只能用前向捕获数据 → 最早 2028 年才有 2 年 OOS；
  - 与本仓库以往研究无关的新机制：可以把历史中一段（例如 2024-07~2026-09）**事先封存**作 OOS，
    前提是这个新机制在那段上从未跑过。
- A 股有文献支撑、且本仓库尚未测过的候选机制（仅供选择，未验证）：
  短期反转（散户过度反应）、MAX/彩票偏好（极端日收益后低收益）、异常换手（关注度溢价）。
  每个都要先写机制、再预登记、再按 G2→G4 顺序跑，任一止损命中即归档。
- 预期：多数候选会失败。这不是流程问题，是市场本身的难度。

### 6 → 8：2–3 个弱相关机制的组合（研究 + 工程）

- 至少 2 个 6 分机制，两两日收益相关 ≤ 0.5（v0 操作化，需你确认）；
- G5：2× 成本为正、3× 不破、容量 ≥ 目标资金 5 倍；
- G8：波动目标、行业/市值中性、相关性约束、回撤与行业暴露预算；
- 组合净 Sharpe ≥ 0.8。

### 8 → 9：时间与独立复制

- G6：Shadow ≥6 个月、滑点/成交率偏差 <30%、涨跌停不可成交如实记录；
- 9 分额外要求前瞻一致记录 ≥12 个月、无衰减、3× 成本仍为正；
- G7：换数据源/市场/制度阶段复现，且由不同实现复算通过。

## 3. 已确定的三项口径（ADR-022，2026-09-25）

用户授权按最合理方案确定，见 `docs/ADR/ADR-022-research-decisions-2026-09-25.md`：

1. 历史 PIT：**捕获 + 规则推定**双口径（rule-v1），报告分开统计；筹码只接受真实捕获。
2. 新机制：`H-20260925-max-lottery`、`H-20260925-abnormal-turnover`、`H-20260925-ep-value`，
   预登记 `docs/prereg/`（已冻结）；OOS `2024-01-01~2026-09-25` 由 `configs/research/oos_seal.json` 封存，
   代码强制、每个假设只可解封一次。
3. 组合弱相关阈值：月度超额收益两两相关 ≤ 0.5。

### 本机下一步命令（按顺序）

```powershell
.\.venv312\Scripts\python.exe scripts\run_g0_audit.py                    # G0 审计（只读）
.\.venv312\Scripts\python.exe scripts\strategy_scorecard.py --register docs\prereg\H-20260925-max-lottery.md
.\.venv312\Scripts\python.exe scripts\strategy_scorecard.py --register docs\prereg\H-20260925-abnormal-turnover.md
.\.venv312\Scripts\python.exe scripts\strategy_scorecard.py --register docs\prereg\H-20260925-ep-value.md
.\.venv312\Scripts\python.exe scripts\strategy_scorecard.py               # 查看分数与阻断
.\.venv312\Scripts\python.exe scripts\sync_namechange.py                   # 历史名称（ST 判定，ADR-022 修订 1）
.\.venv312\Scripts\python.exe scripts\run_cross_section_g2.py --hypothesis H-20260925-max-lottery
.\.venv312\Scripts\python.exe scripts\run_cross_section_g2.py --hypothesis H-20260925-abnormal-turnover
.\.venv312\Scripts\python.exe scripts\run_cross_section_g2.py --hypothesis H-20260925-ep-value
.\.venv312\Scripts\python.exe scripts\strategy_scorecard.py               # 再看 G2 裁决
```

`run_cross_section_g2.py` 只读 IS（2016–2023），读取窗口与封存区间重叠即拒绝；每次运行记入试验台账，
主规格（`--variant 0`）写 `G2.json`，`--variant 1/2` 为 G4 扰动。G2 未通过的假设按止损归档，不解封 OOS、不改定义重跑。

G0 的成本校准项默认不通过：把券商费率确认文件放进证据根，并在 `configs/research/cost_model_v1.json`
填 `broker_statement` 与哈希后重跑审计。其余五项由数据库实测决定。

## 3b. 原待决事项（已由上节决定）

1. **历史 PIT 口径**：标尺要求“全历史 PIT 可用时点”，而库里早期数据是批量入库。二选一：
   - A. 只承认真实捕获时点（最严格）：G0 只能从捕获日起算，历史研究永远不能过 G0；
   - B. 允许“规则推定可用时点”（如行情 T 日 16:00、财报按实际公告日次日开盘），
     以独立字段标注为推定而非捕获，并在报告中分开统计。这是业界常用做法，但必须由你冻结规则。
2. **新机制选择与 OOS 封存段**：选 1–3 个候选机制，并在任何运行之前声明封存的 OOS 区间。
3. **组合“弱相关”阈值**：是否接受 ≤ 0.5。

在 §3 的决定做出之前，工程侧能做的是 G0 的审计报告工具与证据生成，不能替你冻结研究口径。
