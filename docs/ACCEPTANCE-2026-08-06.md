# 验收清单：策略优化闭环 MVP（ACCEPTANCE-2026-08-06）

> **验收对象**：`E:\CODEX\Stock_selection\accumulation_breakout\` 项目「策略优化闭环 MVP」交付结果
> **验收方式**：独立 subagent 依据本清单逐项核验（不访问实现过程对话/设计决策/开发记录/HANDOFF/记忆/git log）
> **环境**：Windows / Python `C:/Python314/python.exe`（已装 pandas/numpy/pydantic/fastapi）
> **数据**：本地 SQLite `runtime/stock_data.db`（399 个交易日，2024-12-10 ~ 2026-08-03，Tushare token 已失效为已知外部限制）
> **已提交**：git commit `bd93635`（仅可用 `git show --stat bd93635` 查看改动文件清单，禁止查看提交说明之外的开发过程）

---

## A. 功能需求（共 8 项）

### A1. 标杆量四象限引擎（bench_volume.py）
- **验收标准**：实现 4 个函数 `find_build_seqs` / `detect_build_seq` / `classify_holding_day` / `bench_exit_events`
- **判定规则**：
  - A1-1 建仓识别：连续「量比(vol/5日均量) ≥ 1.5 且收阳」计为建仓日；允许 1 天「缩量且跌幅 > -2%」断档，第 2 个断档日终止序列
  - A1-2 标杆锁定：序列内**倒数第 2 根放量柱**的量能为标杆（n=1 用当天），锁定后不更新
  - A1-3 四象限：量<标杆且阳→PUSH；量<标杆且阴→WASH；量≥标杆（无论阴阳）→DIST
  - A1-4 二次出货：exit_window(10) 日内累计 2 次 DIST→出场；连续 strong_reset(3) 根非 DIST→计数清零
- **通过标准**：`python -m unittest tests.test_bench_volume` 全部通过（预期 16 个）
- **证据**：测试输出

### A2. 双模式交易模拟器（trade_sim.py）
- **验收标准**：`simulate_trade(bars, entry_i, mode, params)` 支持 fixed/bench 两模式；`summarize` 输出 n_trades/win_rate/profit_factor/max_drawdown/exits
- **判定规则**：
  - A2-1 fixed 模式：次日开盘入场；止损(lo≤entry×0.93)→-7%；止盈(hi≥entry×1.12)→+12%；超时按收盘；**先止损后止盈（保守序）**
  - A2-2 bench 模式：止损 -7% 兜底优先；二次出货确认后**次日开盘**卖出；最长 30 日强平；无止盈目标
  - A2-3 每个模式返回 {ok, ret, days, exit, win, entry, exit_price}，bench 含 max_dd
- **通过标准**：单测通过 + 回归 A2-4：用 `backtest_signals.py` 固定参数（stop 7%/target 12%/hold 15）跑 `--start 20250601 --end 20260731 --step 10 --max-codes 40 --mode fixed`，结果必须与历史基线**逐数字一致**（n_trades=13、win_rate=0.2308、avg_ret=-0.0247、profit_factor=0.453、exits stop:8/time:3/target:2）
- **证据**：测试输出 + 回归命令输出

### A3. 方案 B 入场引擎（entry_plan_b.py）
- **验收标准**：`detect_plan_b(df, ...)` 返回 dict 含 is_breakout/breakout_date/bench_vol/cross_date/cond_*
- **判定规则**：
  - A3-1 金叉：近 20 日内 ma5 上穿 ma10 且信号日 close > ma20
  - A3-2 建仓：信号日前存在已终止建仓序列，放量柱 ≥2 根
  - A3-3 破五：信号日 vol ≥ 标杆量×1.0 且 pct_chg ≥ 2%，涨停日(≥9.8%)排除
  - A3-4 信号日自身放量不得计入建仓序列（必须是之前的序列）
- **通过标准**：`python -m unittest tests.test_entry_plan_b` 全部通过（预期 6 个）
- **证据**：测试输出

### A4. 网格优化器（optimizer.py）
- **验收标准**：`run_grid(start, end, strategy, step, max_codes, grid, ...)` 返回 DataFrame（按 profit_factor 降序）
- **判定规则**：
  - A4-1 网格组合数 = 3×3×3×2 = 54 组合/策略；param_id 为参数 hash 主键
  - A4-2 入场检测结果与出场参数解耦（信号缓存一次、参数重放多次）
  - A4-3 交易日采样：区间内按 step 采样；检测窗口含区间前置 160 日数据（**修复点：前置扩展不得回归**）
  - A4-4 样本内 n_trades < 30 的组合被丢弃
  - A4-5 多进程并行（ProcessPoolExecutor + 分片）
- **通过标准**：`python -m py_compile optimizer.py` + 冒烟：`python optimizer.py --start 20250101 --end 20251231 --strategy A --step 20 --max-codes 120` 输出非空排行榜且 n_trades≥30 过滤生效（若样本不足可放宽断言为「不抛异常且返回 DataFrame」）
- **证据**：命令输出

### A5. 验证框架（walkforward.py）
- **验收标准**：`run_is_oos(strategy, is_start, is_end, oos_start, oos_end, top_n)` + `wf_recheck(combos, windows)`
- **判定规则**：
  - A5-1 IS/OOS 窗口分离：优化器只跑 IS，OOS 只对 Top N 组合验证（不得回灌调参）
  - A5-2 Top N 过滤：win_rate≥0.30 且 max_drawdown≤0.25
  - A5-3 WF 通过线：OOS 平均 PF ≥ 0.8 × IS PF 且每测试窗 DD ≤ 25%
- **通过标准**：py_compile + `python -m py_compile walkforward.py`；结果文件 `runtime/is_oos_A.json` 存在且含 is_top(≥1 行) 与 oos(≤3 行)
- **证据**：命令输出 + 结果文件内容

### A6. 参数注册制 + 擂台赛（strategy_store.py + local_store 表）
- **验收标准**：`seed_params` / `active_weights` / `weekly_arena`；表 strategy_params、param_eval
- **判定规则**：
  - A6-1 表结构：strategy_params(param_id PK, strategy, params_json, status[candidate|active|retired], is_*/oos_* 指标, wf_pass, promoted_at, degrade_streak)；param_eval(param_id, eval_kind, window_start, ...)
  - A6-2 播种：OOS 最优 → active，其余 → candidate
  - A6-3 晋升门槛：candidate PF ≥ active PF × 1.2 且回撤不劣；active 连续 4 周退化→retired
  - A6-4 `active_weights()` 返回 {strategy: OOS PF}
- **通过标准**：`python strategy_store.py --weights` 输出 `{'A': 1.464}`（或任一正数 PF）；`python -c` 查 strategy_params 表有 active 行
- **证据**：命令输出

### A7. 权重回灌到选股排序（scoring.py + run_screener.py）
- **验收标准**：`build_master_score(..., param_weight=1.0)`；run_screener 从 `active_weights()` 取权
- **判定规则**：
  - A7-1 默认 param_weight=1.0 时行为与旧版一致（无 active 参数不改变排序）
  - A7-2 有权重时 total = base × max(weight, 0.1)，detail 含「策略验证权重」
- **通过标准**：py_compile + `python -c` 构造假 sig/fund 行调用 build_master_score 验证 weight 生效（weight=2 时 total 翻倍）
- **证据**：命令输出

### A8. 策略实验室 Web（backend_app.py + 前端 StrategyLab.tsx）
- **验收标准**：路由 POST /api/lab/optimize、GET /api/lab/status、GET /api/lab/leaderboard、GET /api/lab/compare、GET /api/lab/arena；前端页 /lab
- **判定规则**：
  - A8-1 后端启动（127.0.0.1:8000）后 5 个 lab 路由均返回 200 且 JSON 结构合法
  - A8-2 与扫描任务共享 409 互斥（扫描运行中 POST /api/lab/optimize 返回 409）
  - A8-3 前端 dist 包含策略实验室页（`dist/index.html` 引用新 bundle，`App.tsx` 有 /lab 路由）
- **通过标准**：curl 逐个接口 200；`web/frontend` 下 `npx tsc --noEmit` 0 错误
- **证据**：curl 输出 + tsc 输出

---

## B. 边界条件（共 6 项）

### B1. 无信号/空数据不崩溃
- **判定**：`run_grid` 在区间无采样日或全市场无信号时返回空 DataFrame（不抛异常）；`summarize([])` 返回 n_trades=0
- **通过标准**：`python -c` 构造空列表调 summarize；run_grid 传无效区间（如 start>end）不抛异常
- **证据**：命令输出

### B2. 信号日放量不误判为建仓（entry_plan_b）
- **判定**：信号日自身 vol_ratio≥1.5 时，建仓序列必须是信号日**之前**已终止的（A3-4 回归）
- **通过标准**：tests.test_entry_plan_b 的 test_full_signal / test_weak_reattack_rejected 通过
- **证据**：测试输出

### B3. 涨停日排除
- **判定**：pct_chg ≥ 9.8 时方案 B 不出信号
- **通过标准**：test_limit_up_excluded 通过
- **证据**：测试输出

### B4. 停牌/零量
- **判定**：vol≤0 或 NaN 的交易日终止建仓序列，不参与检测，不抛异常
- **通过标准**：test_no_seq（全缩量无序列）通过 + 构造含 0 量行不崩溃
- **证据**：测试输出

### B5. 统计功效门槛
- **判定**：样本内 n_trades < 30 的参数组合不出现在排行榜
- **通过标准**：A4 冒烟输出中无 n_trades<30 的行（或代码审查确认过滤逻辑存在 `df_out[df_out["n_trades"] >= BT_MIN_TRADES]`）
- **证据**：命令输出或代码审查

### B6. 前端请求健壮性
- **判定**：lab 页面请求失败时不白屏（有 err 提示）；轮询 4s 间隔
- **通过标准**：代码审查 StrategyLab.tsx（try/catch + setErr + stopPoll 逻辑）
- **证据**：代码审查记录

---

## C. 质量指标（共 5 项）

### C1. 全量单测
- **判定**：`python -m unittest discover -s tests` 全部通过
- **通过标准**：Ran N tests / OK（预期 ≥36 个，含旧 14 + 新 22）
- **证据**：命令输出

### C2. 编译检查
- **判定**：全部 Python 文件 py_compile 通过
- **通过标准**：`python -m py_compile bench_volume.py entry_plan_b.py trade_sim.py optimizer.py walkforward.py strategy_store.py sync_history.py pipeline_seed.py run_optimize_plan.py backtest_signals.py config.py local_store.py scoring.py run_screener.py web/backend_app.py` 全部 OK
- **证据**：命令输出

### C3. 前端类型检查
- **判定**：`npx tsc --noEmit`（在 web/frontend 下）
- **通过标准**：0 错误
- **证据**：命令输出

### C4. 回测回归一致性（fixed 模式）
- **判定**：A2-4 基线逐数字一致
- **通过标准**：n=13 / 0.2308 / -0.0247 / 0.453 / {stop:8, time:3, target:2}
- **证据**：命令输出

### C5. 结果可复现
- **判定**：`runtime/is_oos_A.json` 与 `runtime/is_oos_B.json` 存在，且 A 的 OOS PF > 1.0、B 的 OOS PF < A 的 OOS PF（与交付结论一致）
- **通过标准**：读文件核对数值
- **证据**：文件内容

---

## D. 数据与结果正确性（共 4 项）

### D1. IS/OOS 结果文件
- **判定**：runtime/is_oos_A.json 含 is_top(≥1) 和 oos(≥1)；字段完整（strategy/vol_ratio_min/strong_reset/exit_window/stop_pct/n_trades/win_rate/profit_factor/max_drawdown）
- **通过标准**：JSON 可解析且字段齐全
- **证据**：文件内容

### D2. 参数注册表状态
- **判定**：strategy_params 表有 1 个 active + ≥2 个 candidate（A 方案）；字段 is_profit_factor/oos_profit_factor 与结果文件一致
- **通过标准**：SQLite 查询 `SELECT param_id, strategy, status, is_profit_factor, oos_profit_factor FROM strategy_params` 输出符合
- **证据**：查询输出

### D3. param_eval 排行榜数据
- **判定**：param_eval 表含 eval_kind='IS' 与 'OOS' 行（A/B 各 ≥4 行）
- **通过标准**：`SELECT eval_kind, COUNT(*) FROM param_eval GROUP BY eval_kind`
- **证据**：查询输出

### D4. 权重回灌实际生效
- **判定**：`strategy_store.py --weights` 输出非空；run_screener._score_codes 中存在 `param_weight_by_tier` 注入逻辑
- **通过标准**：命令输出 + grep 代码
- **证据**：命令输出 + grep 结果

---

## 验收输出格式（每个验收项）

```
[A1-1] 结论：通过 / 未通过 / 需澄清
判定依据：<引用的测试/命令/代码行>
证据：<输出摘要或文件路径>
```

## 汇总要求
1. 逐项输出 A1-A8（含子项）、B1-B6、C1-C5、D1-D4 的结论
2. 未通过项：给出具体原因 + 可执行修复建议（文件+位置+验证命令）
3. 最终结论：整体通过 / 有条件通过 / 未通过
4. 验收过程可追溯：所有命令可重复执行
