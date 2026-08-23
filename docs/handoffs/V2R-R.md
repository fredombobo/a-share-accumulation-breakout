# V2R-R — 可信研究证据包交付（600 股、step=5、净成本 IS/OOS/WF 与反过拟合）

> 本文件由 research-evidence-agent 填写。自报完成不等于验收通过；管理者复验后才会更新任务板。
> 最终声明：**READY_FOR_REVIEW**（未宣布 PERSONAL_INSTITUTIONAL_READY）。

---

## 0. 一页人话结论

**跑了什么**：用当前真实本地历史数据（979 个交易日，覆盖到 20260821），按冻结口径对策略 A 跑了一次完整可信研究：600 只股票、每 5 个交易采样日扫一次、24 个月样本内（IS，20230801–20250731）+ 12 个月样本外（OOS，20250801–20260731）自动窗、含完整净成本（佣金万五+最低5元、卖出印花税千一、其他费万一、双边滑点千一），另加 3 个滚动 Walk-Forward 窗、随机/MA20/60 双基线、PBO/DSR/MinTRL/容量等反过拟合证据。冻结请求只提交了一次，task_id=`1699927499ff`，之后只查状态、未重复提交。

**结论（净成本口径）**：

| 维度 | 结果 | 是否过关 |
|---|---|---|
| 样本 | 979 交易日（≥730，mode=full）；600 股；OOS 净成交 434 笔 | ✅ 充足 |
| OOS 净 PF | 1.076（≥1.0） | ✅ |
| OOS 净胜率 | 39.6%（≥30%） | ✅ |
| OOS 净最大回撤 | **93.2%**（要求 ≤25%） | ❌ 巨幅超限 |
| 三窗 WF 净 PF | 0.699 / 0.748 / 1.241，仅 1/3 窗盈利 | ❌ |
| WF 各窗净回撤 | 96.6% / 85.2% / 81.4%（要求 ≤25%） | ❌ 全部超限 |
| 双基线 | 随机净均收益 -0.55%、MA20/60 -0.19%；主候选 +0.29% 同时跑赢两者 | ✅（但边际极薄） |
| 反过拟合 | WF 盈利一致性 1/3 未达 2/3；PF 保持率 1.24、OOS 排名、邻域稳定均过 | ❌ 总体 FAIL |
| DSR | 0.00（要求 ≥0.95） | ❌ |
| MinTRL | 需 3897 期，当前仅 434 期，覆盖率 0.11 | ❌ |
| CSCV-PBO | 0.3545（要求 ≤0.20） | ❌ |
| 成本压力 | 2× 成本下净 PF 降至 0.957（转亏）、3× 降至 0.851 | ❌ 成本敏感 |
| 容量 | OOS 成交 252 只；最小 ADV20 ≈ 0.27 亿元、中位 ≈ 1.74 亿元；按 5% 参与率单日容量约 133 万元 | ✅ 小资金可容纳 |

**落锤**：`verdict=FAIL`，`candidate_eligible=false`（与门禁一致）。策略 A 在该冻结口径下**净成本回撤不可接受、三窗 WF 大多亏损、DSR/MinTRL/PBO 全部不达标**，虽在净均收益上略赢两个基线，但不足以构成可信 edge。**本任务成功标准是「证据完整可复述」而非策略 PASS——证据已完整，PASS 不成立。** 未把任何参数登记为候选，未进 A 池，未生成任何订单。

**下一步建议**：在证据层面不再围绕该网格继续调参；如要推进，应先在策略信号层（入场/出场逻辑）或股票池构成（当前 600 股为字典序截断，非流动性/市值筛选）上做实质改进，再以同一冻结口径重跑；若要在 Wave 3 谈晋级，需先解决本报告披露的两项研究管线注意点（见 §6：`exit_window` 网格维度在本样本退化为无效、优化器路径缺少 `(code, breakout_date)` 去重）。

---

## 1. 身份

- 任务 ID：**V2R-R**（可信研究证据：600 股、step=5 的净成本 IS/OOS/WF 与反过拟合证据包）
- Agent 角色：**research-evidence-agent**
- 基线 commit：`7bbca60aeeaa150d133d66ebd344f5d1ee7d29fe`（任务板指定 base）
- 分支：`v2r-r`
- worktree 绝对路径：`E:\CODEX\Stock_selection\worktrees\v2r-r`
- 交付 commit：见 §2 `git diff`
- 开始/完成时间（Asia/Shanghai）：本会话 2026-08-23 晚间开始；冻结运行于 2026-08-23T21:40:36 创建、2026-08-23T23:26:13 完成；证据收集 2026-08-23T23:58:58；独立复算验证 2026-08-24T00:26:03→00:27:23。

### 1.1 数据与代码身份

| 项 | 值 |
|---|---|
| 生产库绝对路径 | `E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db` |
| 生产库大小 / mtime | 16,324,935,680 B（15.20 GiB）/ 2026-08-22T23:55:37 |
| 生产库 fingerprint（preflight 只读记录） | `7ff4d9165a19595e2be7470c590d7d286c7db8160814d426fe53cf476dbce35b` |
| 生产库 latest / 交易日数 / 股票数 | `20260821` / 979 / 5698（daily），stock_basic 5551 |
| 研究副本路径（worktree，实际运行所用） | `E:\CODEX\Stock_selection\worktrees\v2r-r\runtime\stock_data.db` |
| 研究副本大小 | 16,325,210,112 B；daily 身份与生产库一致（latest 20260821、979 交易日、5698 代码） |
| 数据充足性 | 979 ≥ 730 → `mode=full`，`enough_trading_days=true`（不属 INSUFFICIENT_EVIDENCE） |
| 代码版本 `code_version`（build_version） | `754dc594cec3`（当前复算一致） |
| 代码 SHA（git HEAD） | `06ca49c9f4b4284172e721230e4ec71cadb7b985` |
| 配置/输入 hash `config_hash`/`input_hash` | `34eac3429a5b676886a98820a7893e39545cdf7f62997ef0a81b46dbab856bf1` |
| 数据版本 `dataset_version` | `9d9074110e0f5032`（独立复算一致 ✅） |
| 成本版本 `cost_version` | `22c7d20200314415` |

> 说明：生产库 mtime/size/fingerprint 自 preflight 记录后未变，研究运行只读写 worktree 副本的 `research_runs` 表，未触碰生产库、纸面账户、订单、成交或任务板。`config_hash` 由 API 在创建运行时置为 `input_hash`（`legacy_lab.py` 语义），两者相同是 API 记录行为，非本任务问题。

## 2. 范围核对

- 实际修改文件：`docs/handoffs/V2R-R.md`（本交付）；研究产物全部写入 gitignore 的 `runtime/v2/research_v2r_r/**`（owned_paths 之一），不入库。
- 是否全部位于 owned_paths：是
- 是否触碰 protected/shared paths：否（未改 `web/backend_app.py`、`app_factory.py`、`configs/platform_v2.yaml`、前端 dist、任务板）
- 是否修改研究代码：否（未发现需要触发「失败测试→修复→重跑」协议的 verdict 级统计缺陷；两处注意点仅作披露，见 §6）
- 未解决的工作区变更：无

```powershell
git status --short        # 空（仅 gitignore 的 runtime/ 等）
git diff --stat 7bbca60aeeaa150d133d66ebd344f5d1ee7d29fe..HEAD
git diff --name-only 7bbca60aeeaa150d133d66ebd344f5d1ee7d29fe..HEAD
```
实际输出（提交后核对）：
```
git diff --stat HEAD
 docs/handoffs/V2R-R.md | 291 +++++++++++++++++++++++++++++++++++++++++++++++++
 1 file changed, 291 insertions(+)
git diff --name-only HEAD
docs/handoffs/V2R-R.md
```

## 3. 冻结请求与运行记录

### 3.1 冻结请求（仅提交一次）

- 端点：worktree 后端 `POST /api/lab/optimize`（:8011）
- 请求体：`strategy=A, mode=grid, max_codes=600, step=5, force=true`
  - note：`frozen V2R-R request: 600 codes, step=5, default GRID_BENCH, auto IS/OOS windows, full net costs`
- 网格（config.GRID_BENCH）：`vol_ratio_min∈{1.3,1.5,1.8}` × `strong_reset∈{2,3,4}` × `exit_window∈{7,10,15}` × `stop_pct∈{0.05,0.07}` = **54 组合**
- 窗：自动 full 窗（`_resolve_lab_windows` → `recommend_research_plan`）
  - IS：20230801–20250731（约 24 个月）
  - OOS：20250801–20260731（约 12 个月）
  - WF 3 窗（日历裁剪后）：
    - WF1 train 20230801–20240731 / test 20240801–20250127
    - WF2 train 20240201–20250127 / test 20250205–20250731
    - WF3 train 20240801–20250731 / test 20250801–20260130
- 成本口径（`ab_screener/domain/costs`，研究/纸面统一）：名义资金 10 万元/笔、100 股整手、佣金双边万五且每边最低 5 元、卖出印花税千一、其他费双边万一、滑点双边万十；停牌/无量/一字涨跌停零成交；滑点后价格夹当日高低价；同日止损优先。

### 3.2 运行时间线（Asia/Shanghai）

| 事件 | 时间 |
|---|---|
| 冻结请求 POST 成功，task_id=`1699927499ff` | 2026-08-23T21:40:16 |
| run 创建/started | 2026-08-23T21:40:36 |
| 全管线完成（IS 网格 54→OOS→WF→基线→门禁→v2 统计） | 2026-08-23T23:26:13 |
| 证据包生成（含 PBO 计算） | 2026-08-23T23:39:22（pbo_matrix.json 23:34:33） |
| 证据包以 `--skip-pbo` 重新生成并入 PBO 块（补全 `pbo=null` 缺口） | 2026-08-23T23:58:58 |
| 独立复算验证 | 2026-08-24T00:26:03 → 00:27:23 |

> 不重复 POST：`submission_manifest.json` 保存 task_id 后，后续只 `GET /api/lab/status?task_id=...` 与读库确认状态（`research_runs.status='done'`，verdict FAIL）。

## 4. 证据结果

### 4.1 主候选（IS 冻结第一名）

`param_id=f3714d6a79f070c2`：`vol_ratio_min=1.3, strong_reset=3, exit_window=7, stop_pct=0.07`

| 指标（净成本） | IS | OOS |
|---|---|---|
| 成交数 | 471 | 434（未成交 1） |
| 胜率 | 39.28% | 39.63% |
| 平均净收益 | -0.48% | +0.29% |
| 净 PF | 0.865 | **1.076** |
| 净最大回撤（账户级复利） | 98.56% | **93.22%** |
| 净盈亏 | -22.6 万元 | +12.5 万元 |

毛口径（仅供诊断）：OOS 毛 PF 1.213、毛胜率 41.15%、毛最大回撤（单笔交易内）29.43%。注意「毛/净」回撤语义不同：净回撤为**按信号日顺序对逐笔净收益保守复利**的账户级曲线回撤；毛回撤为**单笔交易内最大回撤**的最大值（`trade_sim.summarize`）。门禁只读净回撤口径。

### 4.2 三窗 Walk-Forward（主候选净成本）

| 窗 | train PF | test PF | test 胜率 | test 净回撤 | test 成交 |
|---|---|---|---|---|---|
| WF1 | 0.718 | 0.699 | 29.45% | **96.62%** | 163 |
| WF2 | 0.897 | 0.748 | 37.23% | **85.19%** | 137 |
| WF3 | 0.733 | 1.241 | 43.05% | **81.41%** | 302 |

- 盈利一致性：仅 WF3 净 PF≥1（1/3 窗，要求 ≥2/3）→ 不通过
- 稳定性：test 平均 PF 0.896 ≥ 0.8 × train 平均 PF 0.783（=0.626）→ 通过
- 全部 3 窗净回撤远超 25% 上限 → 不通过

### 4.3 双基线对照（OOS 净均收益，同宇宙 600 股、同成本引擎）

| 基线 | 成交 | 净均收益 | 净 PF | 净回撤 |
|---|---|---|---|---|
| 固定种子随机（seed 20260808） | 433 | -0.55% | 0.799 | 97.76% |
| MA20/60 | 428 | -0.19% | 0.941 | 98.27% |
| **主候选** | 434 | **+0.29%** | 1.076 | 93.22% |

主候选净均收益同时 > 两基线 → 两项 `beat_*` 检查通过（但胜出幅度仅约 0.5~0.8 个百分点/笔，且三者在回撤上同为灾难级）。

### 4.4 反过拟合（personal-anti-overfit-v1）

| 检查 | 实际 | 门槛 | 结果 |
|---|---|---|---|
| 参数试验数 | 54 | ≥30 | ✅ |
| 冻结候选 OOS 复核数 | 3 | ≥3 | ✅ |
| IS→OOS 净 PF 保持率 | 1.2439 | ≥0.75 | ✅ |
| 冻结 IS 第一名的 OOS 排名 | rank 1 / 3 | 前半 | ✅ |
| 相邻优选参数 OOS 盈利稳定 | 1.0 | ≥50% 净 PF≥1 | ✅ |
| WF 盈利一致性 | 1/3 | ≥2/3 窗净 PF≥1 | ❌ |

`anti_overfit.verdict=FAIL`。

### 4.5 正式统计（v2_statistics，OOS 434 笔逐笔净收益）

| 指标 | 值 | 达标？ |
|---|---|---|
| 每期 Sharpe | 0.0254 | — |
| 偏度 / 峰度 | 2.82 / 15.73 | — |
| DSR（54 次试验校正） | **0.00** | ❌ ≥0.95 |
| 零假设最大 Sharpe（sr0_null） | 0.8163 | — |
| MinTRL（95% 置信） | **3897.5 期** | 现有 434 期 → 覆盖率 0.111 ❌ |

### 4.6 CSCV-PBO（IS 网格，97 采样日 × 54 组合）

`pbo=0.3545`（>0.20 → FAIL）；logit 中位 0.452；16 折、2145 个对称训练组合、57915 个 logit（内部自洽：C(16,8) 截断至 2000 组合 × 27 列）。

### 4.7 成本压力（主候选 OOS 逐笔，1×/2×/3×）

| 倍率 | 净均收益 | 净胜率 | 净 PF | 净合计 |
|---|---|---|---|---|
| 1× | +0.29% | 39.63% | 1.076 | +1.248 |
| 2× | -0.18% | 38.02% | **0.957** | -0.759 |
| 3× | -0.64% | 36.41% | **0.851** | -2.767 |

模块扁平费率交叉校验（单边 0.0021）方向一致（1× 1.087 / 2× 0.978 / 3× 0.882）。**策略对成本高度敏感：成本翻倍即转亏。**

### 4.8 容量（OOS 成交标的 ADV20 快照）

- OOS 成交 252 只；最小 ADV20 ≈ 2657.8 万元、中位 ≈ 1.74 亿元（按 amount 千元 ×1000 转元）
- 最小流动性标的下单日容量 ≈ 133 万元（5% 参与率 × 5% ADV 上限），权重 1.0/0.5/0.2 三档退出天数均 ≤1 天、capacity_ok=true
- 说明：容量为逐票 ADV20 快照，组合级容量需另行分析；对小资金（10 万元/笔名义）不构成约束。

### 4.9 门禁落锤

- `verdict=FAIL`，`candidate_eligible=false`，`can_claim_edge=false`
- 20 项检查：15 过、5 不过（oos_drawdown、wf1/wf2/wf3 drawdown、wf_consistency）
- 无任何 `research_candidates` 登记（PASS 才登记，本轮 FAIL → 无候选，符合隔离纪律）
- PASS/FAIL 均不会自动进入 A 池或生成订单（本轮亦未发生任何此类副作用）

## 5. 独立复算与验收

### 5.1 独立复算（runtime/v2/research_v2r_r/scripts/verify_metrics.py）

对主候选在 OOS 窗重新回放（600 股、step=5），逐笔净收益重新聚合，并与存储结果比对：

| 项 | 复算 | 存储 | 一致 |
|---|---|---|---|
| OOS 净成交 | 434 | 434 | ✅ |
| 净均收益 | 0.002876 | 0.002876 | ✅ |
| 净胜率 | 0.3963 | 0.3963 | ✅ |
| 净 PF | 1.076 | 1.076 | ✅ |
| 净最大回撤（同复利规则） | 0.9322 | 0.9322 | ✅ |
| Sharpe / DSR / MinTRL | 0.025423 / 0.0 / 3897.48 | 同左 | ✅ |
| dataset fingerprint | 9d9074110e0f5032 | 9d9074110e0f5032 | ✅ |
| 门禁重评 | FAIL / eligible=false | FAIL / false | ✅ |

### 5.2 派工总表 V2R-R 最小质量门（原命令逐条执行）

```powershell
$py = "E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe"
cd E:\CODEX\Stock_selection\worktrees\v2r-r

# 1) Pytest（research + walkforward）
& $py -m pytest tests/test_*research*.py tests/test_*walkforward*.py -q
#   → 37 passed in 132.06s

# 2) Ruff
& $py -m ruff check ab_screener/research
#   → All checks passed!

# 3) Mypy
& $py -m mypy ab_screener/research
#   → Success: no issues found in 20 source files
```

### 5.3 产物与 SHA-256

| 产物（绝对路径） | SHA-256 |
|---|---|
| `...\runtime\v2\research_v2r_r\evidence\evidence_package.json` | `6fa51c38c8e41b1edb4a5a69084337f1db2635151478d945dd30b5a2b0bcf3e3` |
| `...\runtime\v2\research_v2r_r\evidence\evidence_package.md` | `2fa130759e89a5ca97363fce8fd685ed8176424ce62276e34662d63136efdd30` |
| `...\runtime\v2\research_v2r_r\evidence\pbo_matrix.json` | `d50731dd28c414915fd8f58ca4912d41a84dfa0086a773264b6c4d93250f5907` |
| `...\runtime\v2\research_v2r_r\evidence\verify_metrics.json` | `b4cb5c5dc62294cd61ca9e600746bb0c092d9c326b93a6426dd19c4f037976e7` |
| `...\runtime\v2\research_v2r_r\evidence\sha256_manifest.json` | `9af77f013faf8029bf3aa0d04c754f9bf4e973f939f4218b5ce15e1e50f778aa` |

（`...` = `E:\CODEX\Stock_selection\worktrees\v2r-r`；manifest 覆盖上面 4 个数据文件且逐文件校验一致。）

## 6. 发现 / 注意点（不影响本轮 FAIL 落锤，但需如实披露）

1. **`exit_window` 网格维度在本样本退化为无效**。54 组网格中，`exit_window∈{7,10,15}` 对相同 `vol/reset/stop` 给出**逐笔完全相同的出场与收益**（IS 与 OOS 均如此）。30 股小探针复现：27 笔共同交易中 0 笔不同。成因：`bench_exit_events` 的「窗口内二次出货」在 `strong_reset=3` 连续强势清零 + 最长持有 30 日约束下极少被触发。含义：网格**有效独立行为约 18 组**（3×3×2），而非 54 组；多重比较校正（n_trials=54）因此偏保守，不构成虚高。
2. **优化器回放路径缺少 `(ts_code, breakout_date)` 去重**（旧证据路径 `evidence.py` 有 `seen` 去重）。OOS 复算发现 1/433 重复对（`000586.SZ`/20251110），即同一突破在相邻采样日（step=5、6 日窗口重叠 1 日）被计数两次。量化影响：去重后 n=433，净 PF 1.076→1.081、胜率 0.3963→0.3972、均收益 +0.002876→+0.003053、**净最大回撤不变（0.9322）**。不改变任何门禁检查与 verdict。修复点位于 `optimizer.py`（不在 V2R-R owned_paths 内），故只披露、未改动；建议由管理者决定是否在下一波以失败测试先行修复并重跑。
3. **股票池为字典序前 600 只**（`research_universe(max_codes)` 对 `stock_basic ∪ 退市日线` 排序后截断），非按流动性/市值筛选；基线同口径，内部可比，但推广到全市场需谨慎解读。
4. **「毛/净回撤」语义不同**（见 §4.1），门禁统一用净口径，报告文案混用分数与百分号仅为展示问题，不影响比较逻辑。

## 7. 数据与运行证据

- 数据库路径：研究副本 `...\worktrees\v2r-r\runtime\stock_data.db`（生产库 `...\accumulation_breakout\runtime\stock_data.db` 只读校验，mtime/size/fingerprint 未变）
- 数据日期：latest `20260821`，交易日 979，earliest `20220809`
- 数据库 fingerprint：生产库 `7ff4d9165a...`（preflight 记录）；副本 daily 身份一致（未对 16GB 文件全量重算 sha256，以 daily 统计一致性 + dataset_version 复算一致性佐证）
- 代码 SHA：`754dc594cec3`（build_version，当前一致）；git HEAD `06ca49c`
- config hash：`34eac3429a5b676886a98820a7893e39545cdf7f62997ef0a81b46dbab856bf1`（= input_hash）
- 产物路径：`runtime/v2/research_v2r_r/evidence/*`（SHA-256 见 §5.3）
- 是否访问外部数据源：否（全程离线本地库，未用 Tushare/网络）
- 是否包含 Token/账户号：否

## 8. 回滚

- 回滚 commit：本交付只新增 `docs/handoffs/V2R-R.md`，无代码/配置/数据变更，回滚 = 撤销该文档提交即可
- 配置回滚：无
- 数据回滚：研究运行只写 worktree 副本 `research_runs` 一行（task `1699927499ff`），生产库零写入；如需清理，删除 worktree `runtime/` 下研究产物即可，不涉及任何生产数据
- 是否需要停止服务：否
- 是否存在不可逆操作：无

## 9. Agent 自评

- 建议管理者判定：**待验收（READY_FOR_REVIEW）**
- 已知缺陷：无 verdict 级统计缺陷；披露两项研究管线注意点（§6.1 `exit_window` 退化为无效、§6.2 去重缺失），均不改变本轮 FAIL 结论
- 后续依赖：
  - 若推进策略：需先在信号/出场逻辑或股票池层面实质改进后重跑（同一冻结口径）
  - 若修复管线注意点：`optimizer.py` 的去重与 `exit_window` 参数有效性需跨任务（V2R-A/Q2）处理
- 明确声明：本 Agent 未宣布 `PERSONAL_INSTITUTIONAL_READY`；未修改任务板；`LIVE_TRADING_ENABLED` 保持 false；未生成 A 池名单或任何订单。

## 10. 管理者区（实现 Agent 不填）

- 范围审查：
- 代码审查：
- 定向复验：
- 交叉域复验：
- 运行态复验：
- 判定：ACCEPTED / REWORK_REQUIRED / REJECTED
- 缺陷编号：
- 允许进入的下一任务：
