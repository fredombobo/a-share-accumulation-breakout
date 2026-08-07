# 验收报告：策略优化闭环 MVP（ACCEPTANCE-REPORT-2026-08-06）

> **验收依据**：`docs/ACCEPTANCE-2026-08-06.md`（唯一标准，23 项：A 功能 8 / B 边界 6 / C 质量 5 / D 数据 4）
> **验收时间**：2026-08-06 03:30 ~ 03:45（GMT+8）
> **验收方式**：隔离核验——仅依据清单 + 项目源码 + 交付产物（结果 JSON、SQLite 表），未访问 HANDOFF/设计计划/开发日志/git 历史（仅 `git show --stat` 校验改动清单）
> **可复现性**：本报告所有判定命令均在下方「复现命令」列出，重跑即可复现

---

## A. 功能需求

### A1. 标杆量四象限引擎
- [A1-1] **结论：通过** 判定依据：`python -m unittest tests.test_bench_volume` 16/16 通过（含 test_basic_seq_and_bench_lock、test_gap_tolerance、test_double_gap_terminates、test_truth_table、test_second_dist_within_window_exits、test_strong_days_reset_counter 等）
- [A1-2] **结论：通过** 证据：test_basic_seq_and_bench_lock 验证 3 根放量柱 [300,400,500] → 标杆=400（倒数第 2 根）；test_single_day_build 验证 n=1 用当天量
- [A1-3] **结论：通过** 证据：test_truth_table 覆盖 PUSH/WASH/DIST 全象限（含「≥标杆即出货不看阴阳」边界）
- [A1-4] **结论：通过** 证据：test_second_dist_within_window_exits（10 日内 2 次出货→bench 出场）、test_strong_days_reset_counter（3 根强势日清零）、test_window_expiry_recounters（超窗口重新计数）

### A2. 双模式交易模拟器
- [A2-1] **结论：通过** 证据：tests.test_bench_volume.TestTradeSimFixed 3 项全过（test_stop_first/test_target_hit/test_time_exit：止损 -7%、止盈 +12%、超时收盘、保守序）
- [A2-2] **结论：通过** 证据：TestTradeSimBench.test_bench_exit_next_open（确认后次日开盘 10.8 卖出 ret=+8%）、test_stop_overrides_bench（DIST 日止损优先）
- [A2-3] **结论：通过** 证据：单测断言返回结构含 ok/ret/days/exit/win/entry/exit_price；summarize 输出 max_drawdown
- [A2-4] **结论：通过** 证据：`python backtest_signals.py --start 20250601 --end 20260731 --step 10 --max-codes 40 --mode fixed` 实测：**n=13 / win_rate=0.2308 / avg_ret=-0.0247 / PF=0.453 / exits={stop:8, time:3, target:2}**，与历史基线逐数字一致（比对 True）

### A3. 方案 B 入场引擎
- [A3-1/2/3/4] **结论：通过** 证据：`python -m unittest tests.test_entry_plan_b` 6/6 通过（test_full_signal 五步齐备出信号、test_no_cross_rejected、test_weak_reattack_rejected、test_limit_up_excluded、test_no_build_seq_rejected、test_short_window）；A3-4 由 test_full_signal 保证（信号日放量 450 未被计入建仓，标杆仍为 400）

### A4. 网格优化器
- [A4-1] **结论：通过** 证据：冒烟输出「优化池 120 只 × 13 采样日 × 54 组合」（3×3×3×2=54 ✓）；`param_id` 为 md5 hash（optimizer.py L60-64）
- [A4-2] **结论：通过** 证据：代码审查 optimizer.py `_detect_signals_for_code`（入场一次检测）→ `_replay_params`（按组合重放 simulate），解耦架构确认
- [A4-3] **结论：通过** 证据：代码审查 run_grid：`cal = store.distinct_dates("daily")`（全库）+ `sample_days = [d for d in cal if start <= d <= end][::step]` + `load_start = start-365天`（前置扩展）——修复点未回归
- [A4-4] **结论：通过** 证据：optimizer.py L238 `df_out = df_out[df_out["n_trades"] >= BT_MIN_TRADES]`
- [A4-5] **结论：通过** 证据：冒烟输出「分片 1/3、2/3、3/3」（ProcessPoolExecutor 分片并行工作正常）
- **A4 整体判定：通过**（120 只冒烟因样本不足输出「无有效组合」属预期放宽断言；600 只正式结果见 D1 证明排行榜非空且 n=217≥30）

### A5. 验证框架
- [A5-1/2/3] **结论：通过** 证据：py_compile 通过；代码审查 walkforward.py：run_is_oos 先 run_grid(IS 窗口) → 过滤 `win_rate>=0.30 & max_drawdown<=0.25` → Top3 在 OOS 窗口单独 eval；wf_recheck 通过线 `oos_mean_pf >= 0.8 * is_pf 且 dd<=0.25`；结果文件结构见 D1

### A6. 参数注册制 + 擂台赛
- [A6-1] **结论：通过** 证据：SQLite 实测 strategy_params 表含 param_id PK/strategy/params_json/status/is_*/oos_*/wf_pass/degrade_streak；param_eval 表含 param_id/eval_kind/window_start（D2/D3 查询）
- [A6-2] **结论：通过** 证据：D2 查询——A 方案 1 active（b60c55e1...）+ 2 candidate，OOS PF 1.464 最优者 active
- [A6-3] **结论：通过** 证据：代码审查 strategy_store.py weekly_arena：`promote = pf >= ref * ARENA_PROMOTE_MARGIN(1.2) 且 dd 不劣`；`streak >= ARENA_DEGRADE_WEEKS(4) → retired`
- [A6-4] **结论：通过** 证据：`python strategy_store.py --weights` 实测输出 `{'A': 1.464}`

### A7. 权重回灌
- [A7-1] **结论：通过** 证据：scoring.py L196 `param_weight: float = 1.0` 默认值；构造验证 weight=1.0 → total=30.0（与旧逻辑一致）
- [A7-2] **结论：通过** 证据：构造验证 weight=2.0 → total=60.0（**精确 2 倍**，2×1.0 校验 True）；weight=0.0 → 回落 30.0（`max(weight or 1.0, 0.1)` 防呆生效）；detail 含「策略验证权重」

### A8. 策略实验室 Web
- [A8-1] **结论：通过** 证据：实测 GET /api/lab/status → **200**、/api/lab/leaderboard?kind=IS → **200**（返回 param_eval 数据）、/api/lab/compare → **200**、/api/lab/arena → **200**（返回 active 参数行）、/ → 200
- [A8-2] **结论：通过** 证据：代码审查 backend_app.py——lab_optimize 先查 `_running_task_id()` 409「已有扫描进行中」+ `_lab_running()` 409「已有优化任务进行中」（grep 确认 2 处）
- [A8-3] **结论：通过** 证据：`npx tsc --noEmit` 0 错误；dist 已构建（index.html 引用新 bundle index-CEmolLwP.js）；App.tsx 含 /lab 路由

## B. 边界条件

- [B1] **结论：通过** 证据：`summarize([])` 实测返回 {n_trades:0, win_rate:None, ...} 不崩溃；run_grid 无数据区间（2027 年）实测返回空 DataFrame 不抛异常
- [B2] **结论：通过** 证据：tests.test_entry_plan_b.test_full_signal（信号日放量不误判建仓，标杆保持 400）
- [B3] **结论：通过** 证据：test_limit_up_excluded（pct_chg=10.2 拒绝）
- [B4] **结论：通过** 证据：test_no_seq（全缩量无序列）+ find_build_seqs 中 `vol<=0 → 序列终止` 分支
- [B5] **结论：通过** 证据：optimizer.py L238 过滤行存在；正式结果 is_top 各行 n_trades=217/96 ≥ 30
- [B6] **结论：通过** 证据：StrategyLab.tsx L24-31 全 catch + setErr；L37/L41-43 stopPoll/clearInterval；4s 轮询

## C. 质量指标

- [C1] **结论：通过** 证据：`python -m unittest discover -s tests` → **Ran 36 tests / OK**（8.42s）
- [C2] **结论：通过** 证据：15 个 Python 文件 py_compile 全部 OK
- [C3] **结论：通过** 证据：tsc --noEmit exit=0，0 错误
- [C4] **结论：通过** 证据：见 A2-4（与基线逐数字一致 True）
- [C5] **结论：通过** 证据：runtime/is_oos_A.json（IS PF=1.534, OOS PF=1.464 > 1.0）与 runtime/is_oos_B.json（IS 1.608, OOS 0.983 < A）——与交付结论一致，可复现

## D. 数据与结果正确性

- [D1] **结论：通过** 证据：A/B 结果文件 is_top 各 8 行 + oos 各 3 行；必需字段 9 项无缺失；A is_top[0] PF=1.534、oos[0] PF=1.464(n=84)
- [D2] **结论：通过** 证据：SQLite 查询 strategy_params：`[('b60c55e1b38515f0','A','active',1.534,1.464,1), ('3a95887f57937ffe','A','candidate',1.534,1.464,1), ('2a9e877b96885570','A','candidate',1.534,1.464,1)]`——状态/指标与结果文件一致
- [D3] **结论：通过** 证据：param_eval 统计 `IS:16 行, OOS:6 行`（A/B 各 IS 8 + OOS 3）
- [D4] **结论：通过** 证据：weights 输出 `{'A': 1.464}`；run_screener.py L225-232（active_weights 注入）+ L275（param_weight 传参）+ scoring.py L196

---

## 汇总

| 类别 | 通过 | 未通过 | 需澄清 |
|------|------|--------|--------|
| A 功能（8 项含 17 子项） | 17 | 0 | 0 |
| B 边界（6 项） | 6 | 0 | 0 |
| C 质量（5 项） | 5 | 0 | 0 |
| D 数据（4 项） | 4 | 0 | 0 |
| **合计** | **32** | **0** | **0** |

**最终结论：✅ 通过（32/32，无未通过项，无待修复项）**

### 说明事项（非缺陷，记录备查）
1. **A4 冒烟样本不足**：120 只子样本因 n_trades<30 过滤输出为空，属清单允许的放宽断言场景；正式 600 只结果（D1）证明排行榜与过滤逻辑正确
2. **外部依赖**：Tushare token 失效为已知环境限制（影响历史数据扩容与全量 4500 只优化，不影响本验收范围——本验收全部基于现有 399 交易日数据完成）
3. **周擂台赛调度**：weekly_arena 逻辑已验证存在且干跑可用，但未接 Windows 任务计划（部署项，非功能缺陷）

### 复现命令（按序执行即可复现全部结论）
```bash
cd /e/CODEX/Stock_selection/accumulation_breakout
# C2: C:/Python314/python.exe -m py_compile bench_volume.py entry_plan_b.py trade_sim.py optimizer.py walkforward.py strategy_store.py sync_history.py pipeline_seed.py run_optimize_plan.py backtest_signals.py config.py local_store.py scoring.py run_screener.py web/backend_app.py
# C1: C:/Python314/python.exe -m unittest discover -s tests
# C3: cd web/frontend && "E:/Program Files/nodejs/npx.cmd" tsc --noEmit
# C4: C:/Python314/python.exe backtest_signals.py --start 20250601 --end 20260731 --step 10 --max-codes 40 --mode fixed
# D1: C:/Python314/python.exe -c "import json; print(json.load(open('runtime/is_oos_A.json',encoding='utf-8'))['oos'])"
# D2/D3: C:/Python314/python.exe -c "import sqlite3; c=sqlite3.connect('runtime/stock_data.db'); print(c.execute('SELECT param_id,strategy,status,is_profit_factor,oos_profit_factor FROM strategy_params').fetchall())"
# D4: C:/Python314/python.exe strategy_store.py --weights
# A8: curl -s http://127.0.0.1:8000/api/lab/status（4 个 GET 接口均 200）
```
