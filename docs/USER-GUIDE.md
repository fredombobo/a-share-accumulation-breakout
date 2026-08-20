# 量价逻辑平台 · 使用说明（USER GUIDE）

> 版本：v0.4.0（Phase 0~5 全链路） · 宿主：`E:\CODEX\Stock_selection\accumulation_breakout`
> 定位：**研究信号平台，非买卖建议**（全链路 research_only，闸门拦截未过策略）

---

## 1. 一分钟上手

```bat
# 一键启动（重启后端 + 打开研究控制台）
E:\CODEX\Stock_selection\accumulation_breakout\launch_logic_console.bat
```

打开的研究控制台（`runtime/logic_console.html`）提供三个视图：

| 视图 | 能力 |
|------|------|
| **策略库** | 已生成策略列表：状态徽章（draft/research/gated/rejected）+ 回测摘要；点击进详情 |
| **策略详情** | DSL 源码 + 回测指标（总收益/胜率/盈亏比/回撤）+ 闸门逐条 + 出场分布 + 回测历史 |
| **单股研究** | 13 只演示票（API 在线时全库可查）：K线+吸筹区间+突破点+特征卡+**预测条**（p_up/期望收益/失败风险） |
| **生成策略** | 模板表单（参数/闸门阈值）→ 一键跑回测闭环 → 结果卡片（指标+闸门）→ 自动落库 |

> 控制台顶部徽标：`演示数据` = 后端未重启（8001 端口旧实例）；`API 实时` = 新后端已运行，所有视图用真实数据。

---

## 2. 命令行（进阶）

所有命令在项目根目录运行（先 `Remove-Item Env:PYTHONPATH` 清理环境）：

### 2.1 单股解读

```powershell
C:\Python314\python.exe -c "from logic_platform.service import explain; import json; from logic_platform.data.ab_store import ABStore; print(json.dumps(explain('002793.SZ', ABStore()), ensure_ascii=False, indent=2))"
```

### 2.2 全市场结构扫描（研究候选）

```powershell
C:\Python314\python.exe -m logic_platform.cli.run_logic_scan --limit 200 --top 15 --workers 6
```

### 2.3 模板 → 回测 → 闸门（闭环核心）

```powershell
# 默认参数（200 只，约 3~4 分钟）
C:\Python314\python.exe -m logic_platform.cli.run_logic_backtest --template vol_breakout_v1

# 参数覆盖 + 闸门调整 + 指定输出
C:\Python314\python.exe -m logic_platform.cli.run_logic_backtest --template pullback_volume_v1 `
  --max-codes 200 --step 5 --workers 6 `
  --set exit.stop_pct=0.08 --set exit.target_pct=0.15 `
  --gate min_trades=20 --json runtime/logic_bt_result.json
```

退出码：闸门通过（gated）= 0，未通过 = 1。结果自动落库 `logic_strategies` / `logic_backtests`。

### 2.4 训练预测模型（Phase 2）

```powershell
C:\Python314\python.exe -m logic_platform.cli.run_logic_train --codes 200 --horizon 10 --model histgb
# 产物：runtime/logic_models/vN/（model.joblib + meta.json）；最新版本自动生效
```

### 2.5 纸交易闭环（Phase 5）

```powershell
# 当日信号观察卡（仅 gated 策略可投递）
C:\Python314\python.exe -m logic_platform.cli.run_logic_paper --mode signals --strategy vol_breakout_v1
# 历史信号后验命中率（signal_date 后 N 日实际涨跌）
C:\Python314\python.exe -m logic_platform.cli.run_logic_paper --mode backfill --strategy vol_breakout_v1 --days-back 240 --horizon 10
```

---

## 3. API 一览（宿主 8001 端口，前缀 /api/logic）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 功能开关 / lake 状态 / schema 版本 / as_of |
| GET | `/features/{ts_code}` | 近窗特征 + 状态序列 |
| GET | `/explain/{ts_code}` | 人话解读（状态/箱体/量能/预测/理由） |
| POST | `/predict` | 批量推理 `{ts_codes:[...]}` |
| GET | `/strategies` | 策略库列表（闸门状态+回测摘要） |
| GET | `/strategies/{id}` | 策略详情（DSL+回测历史） |
| GET | `/backtest/{run_id}` | 单次回测详情 |
| POST | `/backtest` | 同步执行闭环 `{template, max_codes, step, set:[...], gates:[...]}` |

所有响应带 `research_only: true`；模型/数据缺失时降级提示，不报错。

---

## 4. 内置模板（`logic_platform/dsl/templates/`）

| 模板 | 入场逻辑 | 出场 |
|------|---------|------|
| `vol_breakout_v1` | 状态 ∈ {BREAKOUT, FOLLOW_THROUGH} 且 量比 5/20 ≥ 1.6 | 止损 7% / 止盈 12% / 15 日 |
| `pullback_volume_v1` | 状态 ∈ {FOLLOW_THROUGH, TIGHTENING} 且 缩量 ≥ 3 且 量能分位 ≤ 0.5 且 收盘 ≥ 箱体中轴 | 止损 6% / 止盈 10% / 20 日 |

DSL 语法详见 `docs/DSL-REFERENCE.md`。

---

## 5. 状态机（L1 语义主轴）

```
IDLE → ACCUMULATION（横盘吸筹）→ TIGHTENING（收窄蓄势）→ BREAKOUT（突破）
     → FOLLOW_THROUGH（突破延续）| FAIL（假突破）
```

唯一箱体计算在宿主 `signals.py`（本平台只适配不重算，保证与现网 A 池一致）。

---

## 6. 安全与合规

- **全链路 research_only**：所有输出带该标记；生成逻辑不得直接作为买卖指令（§1.3）
- **闸门 fail-closed**：回测未过（交易数不足/回撤超限/胜率过低）→ rejected/draft，禁止投递纸交易
- **预测降级**：模型缺失时 explain.prediction=None，不伪造结果
- 展示文案禁"必涨/必跌/保证收益"等承诺表述
