# 量价预测 · 逻辑生成平台

> **实现策略：挂在 accumulation_breakout 上扩展，复用 888/ASP 数据湖与现有 Web**  
> 状态：规格文档（供其它 Agent 实现）  
> 更新日期：2026-08-08  
> 宿主项目根：`E:\CODEX\Stock_selection\accumulation_breakout`（以本机实际路径为准）  
> 数据湖旁路：`C:\Users\13818\888`

---

## 0. 给实现 Agent 的一句话任务

在 **accumulation_breakout** 内新增「量价语义 → 预测 → 可回测策略 DSL → 研究台」能力：  
**不新开独立仓库**；热路径与 UI 挂在现有 FastAPI/React；历史深度与因子能力优先读 **888 data_lake / ASP phase 能力**；所有生成逻辑必须经 DSL + 回测闸门，禁止裸 LLM 直接下单。

---

## 1. 产品定义

### 1.1 要做什么

根据 **价格（OHLC）+ 成交量/成交额** 完成三层能力：

| 层级 | 名称 | 职责 | 输出 |
|------|------|------|------|
| L1 | 量价语义 / 形态 | 从 OHLCV 提取特征、识别结构 | 箱体、缩量、放量突破、回踩、背离、状态机序列 |
| L2 | 预测 | 在给定 horizon 估计方向/收益分布 | `p_up`、`expected_ret`、`fail_risk`、置信度 |
| L3 | 逻辑生成 | 把特征+预测变成可解释、可回测规则 | Strategy DSL + 自然语言说明 + 回测报告 |

### 1.2 核心一句话

> **量价数据 → 结构化信号 → 可验证策略逻辑 → 回测/监控闭环**

不是「AI 荐股黑盒」，而是 **可解释研究平台**。

### 1.3 明确非目标（本阶段不做）

- 券商实盘对接 / 自动下单
- 把 Lab / 生成逻辑直接当买卖指令
- 分钟线全市场实时撮合（日线先做扎实）
- 全市场 `fina_indicator` 逐票循环拉取（宿主硬约束）
- 在生产路径用 expanding/adaptive IC 覆盖 ASP 冻结权重（888 生产权重独立，本平台不改）

### 1.4 与现有产品的关系

| 现有能力 | 路径/模块 | 本平台如何用 |
|----------|-----------|--------------|
| 横盘吸筹→启动选股 | `signals.py` / `run_screener.py` / A 池 | L1 默认状态机与默认模板策略的种子 |
| SQLite 热数据 | `runtime/stock_data.db` + `local_store.py` + `sync_daily.py` | 日常扫描、单股查询、Web 主数据源 |
| Web | `web/backend_app.py` + `web/frontend` | 扩展 API 与研究台页面 |
| 纸交易 | `paper_trading/` | 通过闸门的 DSL 可投递纸面信号 |
| 研究/优化 | `research_windows.py` / `optimizer.py` / `walkforward.py` / `strategy_store.py` | 参数搜索与 IS/OOS/WF 纪律 |
| ASP 数据湖 | `C:\Users\13818\888\data_lake` | 长历史 OHLCV、复权、daily_basic、日历（L2 训练/WF） |
| ASP 因子/WF | `888/asp/phase1` `phase2` | 可选：特征对齐、IC 参考、walk-forward 模式 |
| ASP 挖掘旁路 | `888/asp/mine` + promote | 模式参考：候选与生产隔离 |

**两区隔离（强制，继承 RESEARCH-ROADMAP）：**

| 区域 | UI | 可否当「明天买谁」 |
|------|-----|-------------------|
| 可交易研究 | `/` A 池 | 仅候选，仍需人工 |
| 参数 / 逻辑研究 | `/lab` + 新 `/logic` | **否**（生成逻辑默认落研究区） |

---

## 2. 集成架构（挂载方式）

### 2.1 总原则

```
┌──────────────────────────────────────────────────────────────┐
│  accumulation_breakout（宿主 · 产品与 UI 入口）                 │
│  Web / Scan / Paper / signals / scoring / strategy_store       │
│  + 新增: ab_screener/logic/*  或  logic_platform/*               │
└───────────────┬──────────────────────────────▲───────────────┘
                │ 热数据读写                     │ DSL 回测结果
                ▼                              │
     runtime/stock_data.db (SQLite)            │
                │                              │
                │ 可选桥接：历史不足时补读        │
                ▼                              │
┌──────────────────────────────────────────────┴───────────────┐
│  888 data_lake + asp（旁路 · 深度历史 / 因子 / WF 参考）         │
│  prices/daily/*.parquet · adj_factor · daily_basic · meta       │
│  只读优先；禁止本平台写坏 production_weights.json                 │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 数据职责划分

| 数据用途 | 主源 | 备源 | 说明 |
|----------|------|------|------|
| 当日/近窗扫描、单股图 | AB `stock_data.db` | — | 与现网一致；`sync_daily.py` 保活 |
| 长历史训练标签、WF | 888 `data_lake/prices/daily` | AB 扩容后 SQLite | 训练前检查覆盖区间 |
| 复权 | 888 `adj_factor` 或 AB 本地字段 | 统一后复权算信号 | 信号用复权；展示可切换 |
| 换手/市值等 | AB 已有表 或 888 `daily_basic` | — | 特征层抽象，屏蔽来源 |
| 交易日历 | 两边 meta/trade_cal | 以 AB `local_store` 日历为准做 as_of | 新鲜度按**交易日**滞后 |

### 2.3 推荐包结构（在宿主内新增）

```text
accumulation_breakout/
  logic_platform/                 # 新增包（或 ab_screener/logic/）
    __init__.py
    README.md                     # 指向本文档
    # --- L1 特征与语义 ---
    features/
      ohlcv_features.py           # ret/ATR/均线偏离等
      volume_features.py          # 量比/分位/OBV 类
      structure_features.py       # 对 signals.py 箱体结果再封装
    structure/
      state_machine.py            # IDLE→ACCUMULATION→...→FAIL
      adapters_signals.py         # 包装 detect_accumulation_breakout
    # --- L2 预测 ---
    prediction/
      labels.py                   # 未来 N 日收益/新高/回撤标签
      dataset.py                  # 从 store / lake 拼面板
      models.py                   # baseline + LightGBM 接口
      serve.py                    # 推理：输入特征 → 概率
    # --- L3 逻辑生成 ---
    dsl/
      schema.py                   # Strategy DSL pydantic 模型
      parser.py
      interpreter.py              # DSL → 信号序列 / 订单意图
      templates/                  # 内置模板 YAML
        vol_breakout_v1.yaml
        pullback_volume_v1.yaml
    generate/
      template_fill.py            # 模板 + 参数网格/Optuna
      optimize.py                 # 对接现有 optimizer / walkforward
      llm_optional.py             # 可选：只改 DSL + 说明，不执行
      gates.py                    # 上架闸门
    # --- 数据桥 ---
    data/
      ab_store.py                 # 封装 local_store
      lake_bridge.py              # 只读 888 parquet
      feature_store.py            # 物化 features_daily（可选 SQLite 表）
    # --- 服务 ---
    api/
      routes.py                   # 挂到 backend_app 的 router
    cli/
      run_logic_scan.py
      run_logic_backtest.py
      run_logic_generate.py
  docs/
    VOLUME-PRICE-LOGIC-PLATFORM.md  # 本文
```

**不要**把核心逻辑只写在 `web/backend_app.py` 巨石里；backend 只挂 router。

### 2.4 与现有模块的调用关系

```text
logic_platform.structure.adapters_signals
    → signals.detect_accumulation_breakout / evaluate_box_window

logic_platform.dsl.interpreter
    → 产出与 paper_trading.signals / trade_plan 可对齐的 entry/exit 意图

logic_platform.generate.optimize
    → research_windows / walkforward / optimizer / strategy_store（擂台模式）

logic_platform.data.ab_store
    → local_store（每操作新连接；ON CONFLICT DO UPDATE）

logic_platform.data.lake_bridge
    → 888 data_lake parquet（path 可配置，默认 C:\Users\13818\888\data_lake）
```

### 2.5 配置

在 `config.py` 或 `configs/logic_platform.yaml` 增加（示例键名）：

```yaml
logic_platform:
  enabled: true
  lake_root: "C:/Users/13818/888/data_lake"   # 可环境变量 LOGIC_LAKE_ROOT
  lake_readonly: true
  default_horizon_days: [5, 10, 20]
  feature_materialize: true                   # 是否写 features_daily
  dsl_dir: "logic_platform/dsl/templates"
  require_gate_for_paper: true                # 未过闸门禁止进纸交易
  research_only_default: true
```

环境变量：

| 变量 | 用途 |
|------|------|
| `TUSHARE_TOKEN` | 宿主已有，不写进 yaml |
| `LOGIC_LAKE_ROOT` | 覆盖 data_lake 路径 |
| `LOGIC_PLATFORM_ENABLED` | 功能开关 |

---

## 3. 分层架构（逻辑视图）

```
┌─────────────────────────────────────────────────────────────┐
│  Presentation                                                │
│  多图看板 / 单股深研 / DSL 编辑器 / 回测报告 / 告警            │
│  路由建议: /logic  /logic/:code  /logic/strategies            │
└────────────────────────────▲────────────────────────────────┘
                             │ REST（沿用 :8001）
┌────────────────────────────┴────────────────────────────────┐
│  Application                                                 │
│  Explain · GenerateLogic · Backtest · Promote-to-Lab         │
│  （不直接 Promote-to-A-pool，除非人工+闸门）                   │
└────────────────────────────▲────────────────────────────────┘
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   Feature Store      Prediction Svc      Logic Engine
   (量价因子)         (统计/ML)           (模板+优化+DSL)
          ▲                  ▲                  ▲
          └──────────────────┼──────────────────┘
                             ▼
┌────────────────────────────────────────────────────────────┐
│  Data Plane                                                 │
│  AB SQLite 热路径  +  888 Parquet 冷/深历史  +  可选物化表   │
└────────────────────────────────────────────────────────────┘
```

---

## 4. 量价语义（L1）— 策略内核

### 4.1 特征字典（第一版必须实现）

**价格类**

| 特征 | 定义要点 | 用途 |
|------|----------|------|
| `ret_1/5/20` | 收益率 | 动量/反转 |
| `atr_14` | 真实波幅 | 止损/仓位 |
| `box_amp` | 箱体振幅（稳健，去影线极值） | 吸筹质量 |
| `dist_ma20/60` | 相对均线偏离 | 过热/乖离 |
| `days_from_box_end` | 距箱体右端 | 时效 |
| `dist_high_60` | 距 60 日高 | 近高突破 |

**量能类**

| 特征 | 定义要点 | 用途 |
|------|----------|------|
| `vol_ma_ratio_5_20` | 5 日均量 / 20 日均量 | 放量/缩量 |
| `vol_percentile_60` | 60 日量能分位 | 极端量 |
| `shrink_days` | 连续缩量天数 | 吸筹加分 |
| `breakout_vol_mult` | 突破日量 / 箱体均量 | 突破确认 |
| `amount_ratio` | 成交额比（若有） | 比手更稳 |
| `vp_corr_20` | 近 20 日价量相关 | 背离辅助 |

**结构类（优先复用 signals.py）**

| 特征 | 来源 |
|------|------|
| 箱体 left/right/high/low/质量分 | `evaluate_box_window` / `_find_best_box` |
| 突破日、突破强度 | `detect_accumulation_breakout` / `score_breakout_strength` |
| 支撑压力触及次数、R² 拒通道 | 已有箱体专业判定 |

**宿主箱体硬参数（勿静默改默认生产阈值；研究区可覆盖）**

- `BOX_MIN_DAYS=20` … `BOX_MAX_DAYS=125`
- `BOX_MAX_AMP=0.28`
- `HORIZON_DAYS=160`
- 突破窗口：近 `BREAKOUT_WINDOW_DAYS=5`（勿要求必须「今天」）
- 缩量：加分项，不要做成硬门槛（与 AGENTS.md 一致）

### 4.2 状态机（可解释主轴）

```text
IDLE
  → ACCUMULATION   箱体振幅收敛 + 量能中位/偏缩 + 时长≥N
  → TIGHTENING     振幅再收窄 + 量能再缩（可选子状态）
  → BREAKOUT       收盘破上沿 + 量比≥阈值 + 非一字涨停僵局（研究可配）
  → FOLLOW_THROUGH 突破后 2–5 日不破平台 + 回踩缩量
  → FAIL           假突破：放量滞涨 / 快速跌回箱体
```

实现要求：

1. 每日（或每个 as_of）输出 `state`、`state_since`、`transition_reason[]`
2. 可序列化为 JSON，供前端 K 线 markArea / markPoint
3. `adapters_signals.py` 把现有 `detect_accumulation_breakout` 结果映射到状态，避免两套互相打架
4. 日期格式：内部统一 `YYYYMMDD`；API 可双出；ECharts 轴对齐时 normalize

### 4.3 默认业务叙事（产品话术）

沿用现有「横盘吸筹 → 启动」：  
综合分参考（现网）信号 50% + 资金 25% + 基本面 25%；本平台 L1 先把 **信号侧** 结构化，资金/基本面继续走 `scoring.py`，不必在 MVP 重做。

---

## 5. 预测层（L2）

### 5.1 标签（先做这些）

| 标签 | 定义 | 用途 |
|------|------|------|
| `y_up_n` | 未来 N 日 `close` 收益 > 0 | 分类 |
| `y_ret_n` | 未来 N 日收益 | 回归 |
| `y_mdd_n` | 未来 N 日最大回撤 | 风险 |
| `y_new_high` | 未来 N 日是否创新高 | 趋势延续 |

默认 N ∈ {5, 10, 20}。标签必须 **shift 未来**，训练时严格无泄漏。

### 5.2 模型策略

1. **Baseline**：逻辑回归 / 阈值规则（状态=BREAKOUT 时的历史胜率表）
2. **主模型**：LightGBM（表格特征），样本按交易日 walk-forward
3. **输出**：`p_up`、`expected_ret`、`fail_risk`、`model_version`、`train_window`
4. **启用条件**：默认仅在 `ACCUMULATION|TIGHTENING|BREAKOUT` 附近推理，降低噪音

### 5.3 训练数据来源优先级

1. 若 AB `research_status` 为 `full` 且本地历史足够 → 优先 SQLite 一致性  
2. 否则 `lake_bridge` 读 888 日线做训练面板，推理仍可用 AB 最新 bar  
3. 训练产物落 `runtime/logic_models/`（gitignore），元数据 JSON 入库

### 5.4 禁止

- 用当日未完成 bar 当收盘特征做「已确认突破」生产信号（盘中最多 `WATCH`）
- 把预测概率单独当买卖点（必须进入 DSL 条件）

---

## 6. 逻辑生成（L3）— Strategy DSL

### 6.1 为什么必须 DSL

- 可版本化、可 diff、可回测  
- LLM 只允许改 DSL，不允许直接改生产 Python  
- 与纸交易、报告、前端展示共用同一契约  

### 6.2 DSL Schema（YAML/JSON 等价）

```yaml
strategy:
  id: vol_breakout_v1
  version: "1.0.0"
  name: "量价突破确认"
  research_only: true
  universe: "all_a"          # all_a | hs300 | custom
  as_of_policy: "bar_close"  # 仅收盘确认

  entry:
    all:
      - { feature: "structure.state", op: "in", value: ["BREAKOUT", "FOLLOW_THROUGH"] }
      - { feature: "vol_ma_ratio_5_20", op: ">=", value: 1.6 }
      - { feature: "pred.p_up_10", op: ">=", value: 0.55 }
    any: []                  # 可选 OR 组

  exit:
    any:
      - { feature: "close", op: "<", ref: "box_mid" }
      - { feature: "ret_from_entry", op: ">=", value: 0.12 }
      - { feature: "atr_stop", op: "mult", value: 2.0 }
      - { feature: "hold_days", op: ">=", value: 20 }

  position:
    method: "fixed_pct"      # fixed_pct | vol_target
    max_pct: 0.10
    max_names: 15

  risk:
    avoid: ["st", "limit_up_open_chase"]
    regime_block: true       # 防守期禁止新开（对齐 market_regime）

  meta:
    template: "vol_breakout_v1"
    generated_by: "template_fill|optimizer|llm"
    parent_signal: "accumulation_breakout"
```

### 6.3 解释器职责

输入：`as_of` + universe + DSL + feature panel  
输出：

```json
{
  "signals": [
    {
      "ts_code": "601857.SH",
      "side": "buy",
      "reason": ["state=BREAKOUT", "vol_ratio=1.82", "p_up_10=0.58"],
      "stop": 7.85,
      "target": 8.90,
      "state": "BREAKOUT"
    }
  ],
  "dsl_id": "vol_breakout_v1",
  "as_of": "20260807"
}
```

### 6.4 生成流水线

```text
用户意图 / 模板选择
    → 相似形态检索（可选：序列 embedding，MVP 可跳过）
    → 模板 + 参数网格 或 Optuna
    → 批量回测（成本/滑点/涨跌停）
    → gates.py 稳健性过滤
    → （可选）LLM：把最优参数写成中文说明 + 精炼 DSL 字段
    → 写入策略库 status=research
    → 人工确认后才允许 paper 或 A 池实验旗标
```

### 6.5 闸门 gates（上架前必须）

| 检查 | 最低要求（可配置，下列为建议默认） |
|------|----------------------------------|
| 样本外分段 | 至少按年或按 IS/OOS 窗 |
| 最少交易次数 | ≥ 30（全样本）或研究窗内可配置下调但标记 degraded |
| 最大回撤 | 可配置上限 |
| 成本敏感性 | 手续费/滑点上调后不翻号 |
| 未来函数 | 静态检查 DSL 特征仅用 t 及以前 |
| research_status | `insufficient` 禁止宣称 edge；`degraded` 仅摸底 |
| 与 A 池话术隔离 | `research_only=true` 默认 |

未过闸门：`status=rejected` 或 `draft`，**禁止** `require_gate_for_paper` 路径进入纸交易。

### 6.6 内置模板（MVP 两套即可）

1. **`vol_breakout_v1`**：箱体突破 + 量比 + 可选 p_up  
2. **`pullback_volume_v1`**：突破后回踩缩量 + 不破箱体中轴  

参数与现网 `configs/default_strategy_profile.json` / `strategy_store` 对齐处写清映射表。

---

## 7. 数据模型（建议表 / 文件）

### 7.1 SQLite（`runtime/stock_data.db` 内新表，迁移脚本必写）

```sql
-- 日频特征物化（可选但推荐）
CREATE TABLE IF NOT EXISTS features_daily (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,  -- YYYYMMDD
  feature_version TEXT NOT NULL,
  payload_json TEXT NOT NULL, -- 或拆关键列 + JSON 扩展
  PRIMARY KEY (ts_code, trade_date, feature_version)
);

CREATE TABLE IF NOT EXISTS structure_state_daily (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  state TEXT NOT NULL,
  box_high REAL,
  box_low REAL,
  box_mid REAL,
  breakout_date TEXT,
  reasons_json TEXT,
  PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS logic_strategies (
  id TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  name TEXT NOT NULL,
  dsl_yaml TEXT NOT NULL,
  status TEXT NOT NULL,       -- draft|research|gated|rejected|archived
  research_only INTEGER NOT NULL DEFAULT 1,
  metrics_json TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS logic_backtests (
  run_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  params_json TEXT,
  window_json TEXT,           -- IS/OOS/WF
  metrics_json TEXT,
  equity_path TEXT,           -- 可选文件路径
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS logic_predictions (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  model_version TEXT NOT NULL,
  horizon INTEGER NOT NULL,
  p_up REAL,
  expected_ret REAL,
  fail_risk REAL,
  PRIMARY KEY (ts_code, trade_date, model_version, horizon)
);
```

迁移风格对齐 `paper_trading/migrations.py`（可逆、可重复执行）。

### 7.2 888 只读约定

- 根：`{lake_root}/prices/daily/YYYYMMDD.parquet`
- 复权：`fundamentals/adj_factor/`
- 日历：`meta/trade_calendar.parquet`
- **只读**；质量报告可读 `quality/reports/`
- 路径不存在时：功能降级并在 API `warnings[]` 返回，不崩溃

---

## 8. API 设计（挂载 FastAPI）

前缀建议：`/api/logic`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/logic/health` | 开关、lake 是否可见、feature_version |
| GET | `/api/logic/features/{ts_code}` | 近窗特征 + 状态序列 |
| GET | `/api/logic/explain/{ts_code}` | 人话解释：为何是某状态 |
| POST | `/api/logic/scan` | 异步扫描（复用 scan job 模式） |
| GET | `/api/logic/scan/{job_id}` | 进度/结果 |
| POST | `/api/logic/predict` | 批量推理 |
| GET | `/api/logic/strategies` | 策略库列表 |
| POST | `/api/logic/strategies` | 保存 DSL draft |
| POST | `/api/logic/generate` | 模板填充/优化生成 |
| POST | `/api/logic/backtest` | 跑回测 |
| GET | `/api/logic/backtest/{run_id}` | 报告 |
| POST | `/api/logic/gates/evaluate` | 对某 strategy 跑闸门 |
| POST | `/api/logic/promote` | research→gated（人工确认 body） |

约束：

- Pydantic body 模型定义在引用路由之上（宿主 Web 约定）
- 扫描类长任务：立即返回 job_id，结果落 `runtime/logic_jobs/`
- 所有推荐类响应带 `as_of`、`data_freshness`、`research_only`

---

## 9. 前端（复用 web/frontend）

### 9.1 页面

| 路由 | 内容 |
|------|------|
| `/logic` | 策略库 + 生成入口 + 闸门状态 |
| `/logic/lab` | 与 `/lab` 打通或嵌套：参数/回测 |
| `/logic/stock/:code` | 单股：K 线+量能+状态带+预测+DSL 命中原因 |
| 可选多股宫格 | 研究台多窗（对标用户截图），二期 |

### 9.2 单股深研必备组件

1. K 线 + 成交量（ECharts 已有可扩展 `charting.py` / 前端图表）  
2. 状态 markArea（吸筹区间）+ 突破 markPoint  
3. 侧栏：特征表、预测条、DSL 条件勾选命中  
4. 「生成逻辑草案」按钮 → 仅 research  
5. 「跑回测」→ 展示 metrics，不写 A 池  

### 9.3 文案合规

- 默认文案：「研究信号 / 逻辑草案」，禁止收益承诺  
- 防守期、数据过期沿用现网 banner  

---

## 10. 回测与执行闭环

### 10.1 回测引擎选择

优先级：

1. 复用/扩展宿主 `backtest_signals.py`、`trade_sim.py`、`walkforward.py`  
2. 需要组合层时参考 `888/asp/backtest/engine.py`、`phase1/walkforward.py`（**复制模式或薄适配，避免强耦合改 888 生产**）  
3. 纸交易：DSL 解释器输出 → `paper_trading` 信号格式  

### 10.2 成本与约束（必须建模）

- 佣金 + 印花税（卖）+ 滑点  
- 涨跌停不可买/不可卖  
- 停牌跳过  
- 体积：单 bar 成交量占比上限（可选）  

### 10.3 闭环

```text
DSL gated
  → 每日 as_of 解释器
  → 观察池 / 纸交易
  → 后验命中率看板（logic_backtests + 实盘后标注）
```

---

## 11. 分阶段实现计划（给 Agent 拆任务）

### Phase 0 — 数据契约与骨架（约 3–5 天）

**目标：** 包结构、配置、lake 只读桥、health API、空路由挂载。

- [ ] 创建 `logic_platform/` 包与本文档链接  
- [ ] `lake_bridge.py`：读一天 parquet、读单票历史、日历  
- [ ] `ab_store.py`：封装 local_store 取 OHLCV  
- [ ] SQLite 迁移：上表（可先最小 `logic_strategies`）  
- [ ] `backend_app` include_router  
- [ ] 单测：lake 缺失不崩；store 能取到一根日线  

**验收：** `GET /api/logic/health` 返回 enabled + lake_ok/lake_missing。

### Phase 1 — 量价语义引擎（约 1–2 周）

**目标：** 特征 + 状态机 + 单股 explain + 全市场 structure scan。

- [ ] `ohlcv_features` / `volume_features`  
- [ ] `adapters_signals` 映射现有突破信号  
- [ ] `state_machine` 日序列  
- [ ] `GET /api/logic/features/{code}` `GET /api/logic/explain/{code}`  
- [ ] CLI `run_logic_scan.py` 输出 Top 结构候选  
- [ ] 前端单股状态带（可先 API-only）  

**验收：** 对已知突破票，explain 理由与 `signals.py` 结论一致或可 diff 说明。

### Phase 2 — 预测服务（约 1–2 周）

**目标：** 标签 + baseline + 可选 LightGBM + 落库。

- [ ] `labels.py` + 无泄漏断言测试  
- [ ] baseline 条件概率表  
- [ ] walk-forward 训练脚本  
- [ ] `POST /api/logic/predict`  
- [ ] `research_status` 联动：insufficient 时降级提示  

**验收：** 单元测试证明 label 未使用未来 bar；推理 API 返回 model_version。

### Phase 3 — DSL + 模板生成 + 回测闸门（约 2–3 周）

**目标：** 两套模板、解释器、回测、gates、策略库。

- [ ] pydantic DSL schema  
- [ ] interpreter → 信号列表  
- [ ] template_fill + 网格/Optuna  
- [ ] backtest 对接  
- [ ] gates 与 status 流转  
- [ ] UI `/logic` 列表与详情  

**验收：** 一键从模板生成 → 回测 → pass/fail 闸门可复现。

### Phase 4 — 研究台 UI（并行，约 1–2 周）

- [ ] 单股深研页  
- [ ] 多股宫格（二期可砍）  
- [ ] 与 `/lab` 导航隔离文案  

### Phase 5 — 纸交易闭环（约 1 周）

- [ ] gated DSL → paper signal  
- [ ] 命中率后验报表  
- [ ] 文档与操作手册条目  

### MVP 可演示定义（建议对外只承诺这个）

1. 日线 OHLCV（AB）  
2. 状态机 + explain  
3. 两套 DSL 模板  
4. 回测 + 闸门  
5. 单股页：图 + 状态 + 原因 + 报告  
6. 全市场 structure scan Top N  

---

## 12. 宿主硬约束（实现时禁止踩坑）

摘自 `AGENTS.md`，**对本平台同样强制**：

1. Tushare 只从 `tushare_init.py` 取 pro；curl_cffi；禁止裸 requests 直连指纹站  
2. 禁止全市场 `fina_indicator` 循环  
3. SQLite 每操作新连接；`ON CONFLICT DO UPDATE`，禁止 `INSERT OR REPLACE` 静默 NULL  
4. sync 按交易日历 diff 补洞  
5. 推荐前核对 `as_of` / `max_trade_date('daily')`  
6. 资金流单位万元；勿重复 /100  
7. 突破日与 ECharts 轴日期 normalize  
8. A 池 vs Lab 隔离；本平台默认 research  
9. Python 环境注意清理 `PYTHONPATH` / 代理污染  

888 侧额外：

10. 不修改 `config/production_weights.json` 生产冻结权重  
11. data_lake **只读**；写操作仅允许在 AB `runtime/`  

---

## 13. 测试与验收清单

### 13.1 自动化

```powershell
cd E:\CODEX\Stock_selection\accumulation_breakout
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null

# 现有回归不得破坏
C:\Python314\python.exe -m pytest tests/ test_signals.py -q

# 新增（实现时补齐）
C:\Python314\python.exe -m pytest tests/test_logic_platform/ -q
```

建议新增测试文件：

- `tests/test_logic_platform/test_features_no_lookahead.py`  
- `tests/test_logic_platform/test_state_machine.py`  
- `tests/test_logic_platform/test_dsl_schema.py`  
- `tests/test_logic_platform/test_interpreter.py`  
- `tests/test_logic_platform/test_gates.py`  
- `tests/test_logic_platform/test_lake_bridge_missing.py`  
- `tests/test_logic_platform/test_api_logic_health.py`  

### 13.2 手工验收

| # | 步骤 | 期望 |
|---|------|------|
| 1 | health | enabled，库连接 OK |
| 2 | 选一只有箱体突破历史的票 explain | 状态与理由可读 |
| 3 | 跑模板 backtest | metrics JSON + 无未来函数 |
| 4 | 闸门 fail 样例 | 无法 paper |
| 5 | 闸门 pass 样例 | status=gated，research 文案仍在 |
| 6 | 现网扫描 A 池 | 行为与扩展前一致 |

---

## 14. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 与 signals 双实现漂移 | 唯一箱体计算在 `signals.py`；logic 只适配 |
| 过拟合「生成逻辑」 | 强制 OOS/WF + 最少交易次数 + research_only |
| 888 路径换机失效 | `LOGIC_LAKE_ROOT` + health 降级 |
| 巨石 backend | router 分包，逻辑在 logic_platform |
| 用户把研究当实盘 | UI 文案 + promote 人工确认 + 默认 research_only |
| 数据新鲜度 | 复用 data_freshness；过期禁用「今日信号」话术 |

---

## 15. 关键文件索引（实现导航）

| 用途 | 路径 |
|------|------|
| 宿主约定 | `AGENTS.md` `FOR_AGENTS.md` |
| 研究纪律 | `docs/RESEARCH-ROADMAP.md` |
| 箱体/突破 | `signals.py` |
| 打分 | `scoring.py` |
| 配置 | `config.py` `configs/default_strategy_profile.json` |
| 本地库 | `local_store.py` |
| 日更 | `sync_daily.py` `sync_history.py` |
| 扫描 | `run_screener.py` `parallel_scan.py` `scan_job_runner.py` |
| 回测 | `backtest_signals.py` `trade_sim.py` `walkforward.py` |
| 策略擂台 | `strategy_store.py` `optimizer.py` |
| Web API | `web/backend_app.py` `ab_screener/api/` |
| 纸交易 | `paper_trading/` |
| 888 湖说明 | `C:\Users\13818\888\DATA_LAKE.md` |
| 888 产品 | `C:\Users\13818\888\README_ASP.md` `docs/ASP_*.md` |
| 本文 | `docs/VOLUME-PRICE-LOGIC-PLATFORM.md` |

---

## 16. 给下游实现 Agent 的推荐开工顺序

1. 读 `AGENTS.md` + 本文 §12 硬约束  
2. Phase 0 骨架 + health  
3. Phase 1 状态机与 explain（用户立刻有体感）  
4. Phase 3 的 DSL 最小解释器（可先无 ML）  
5. 回测 + gates  
6. Phase 2 预测增强  
7. UI `/logic`  
8. 纸交易对接  

**原则：先规则可解释，再 ML 提精度，最后可选 LLM 填 DSL 说明。**

---

## 17. 附录 A — 单股 explain 响应示例

```json
{
  "ts_code": "601857.SH",
  "as_of": "20260807",
  "state": "ACCUMULATION",
  "box": {"high": 8.42, "low": 7.91, "amp": 0.064, "days": 48},
  "volume": {"vol_percentile_60": 0.35, "vol_ma_ratio_5_20": 0.82, "shrink_days": 6},
  "prediction": {
    "horizon": 10,
    "p_up": 0.58,
    "expected_ret": 0.042,
    "fail_risk": 0.37,
    "model_version": "baseline_v0"
  },
  "reasons": [
    "近48个交易日箱体振幅6.4%（≤28%）",
    "量能分位35%，偏缩量吸筹",
    "未收盘突破上沿，状态未升至BREAKOUT"
  ],
  "suggested_dsl_id": "vol_breakout_v1",
  "research_only": true,
  "data_freshness": {"ok": true, "max_trade_date": "20260807"}
}
```

---

## 18. 附录 B — 与「截图多股看板」的映射

用户研究台期望（多股 K 线+量柱宫格）：

| UI 元素 | 数据来源 |
|---------|----------|
| 多股宫格 | logic scan Top N 或 A 池子集 |
| 单图 K+量 | AB OHLCV |
| 突破/平台标注 | structure_state_daily |
| 底部量能颜色 | vol_ma_ratio / 分位 |
| 点击进深研 | `/logic/stock/:code` |

MVP 可先单股深研 + 列表；宫格二期。

---

## 19. 附录 C — 变更记录

| 日期 | 说明 |
|------|------|
| 2026-08-08 | 初版：挂载 AB + 复用 888 的架构/策略/DSL/分期实现规格 |
| 2026-08-08 | **Phase 0+1 完成**：logic_platform 包上线（骨架/lake 桥/迁移101/health/特征/状态机/explain API/CLI）；验收见 `LOGIC-PLATFORM-PHASE1-ACCEPTANCE-2026-08-08.md`；宿主回归 220 passed |
| 2026-08-08 | **Phase 3 完成**：DSL（schema/parser/interpreter/2 模板）+ 回测引擎 + 闸门 + 闭环 CLI（run_logic_backtest）；语法参考 `DSL-REFERENCE.md`，验收见 `LOGIC-PLATFORM-PHASE3-ACCEPTANCE-2026-08-08.md`；76 passed |
| 2026-08-08 | **Phase 2 完成**：预测服务（labels 无泄漏/dataset 时间序切分/models logistic+histgb+stats/serve Predictor/训练 CLI/explain 带 prediction/pred.* 激活/POST /api/logic/predict）；验收见 `LOGIC-PLATFORM-PHASE2-ACCEPTANCE-2026-08-08.md`；94 passed |
| 2026-08-08 | **Phase 4+5 + 交付完成（v0.4.0）**：策略库/回测报告 API + 研究控制台（logic_console.html 四视图）+ 纸交易闭环（观察卡+后验）+ 一键启动 + 三份交付文档（USER-GUIDE / DEPLOYMENT / FINAL-ACCEPTANCE）；99 passed；见 `FINAL-ACCEPTANCE.md` |

---

**文档结束。** 实现 Agent 完成任一 Phase 后，请在 `docs/` 追加 `LOGIC-PLATFORM-PHASE{N}-ACCEPTANCE-YYYY-MM-DD.md`，并跑 §13 测试清单。
