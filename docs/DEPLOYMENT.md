# 量价逻辑平台 · 部署步骤（DEPLOYMENT）

> 目标环境：Windows 单机（宿主机已装 Python 3.14 + 数据已就绪）

---

## 1. 环境要求

| 项 | 要求 | 检查命令 |
|----|------|---------|
| Python | 3.14（宿主约定 `C:\Python314\python.exe`） | `C:\Python314\python.exe --version` |
| 依赖 | 宿主已装（fastapi/pandas/pyarrow/sklearn/joblib/pyyaml 等） | `C:\Python314\python.exe -m pytest tests/test_logic_platform/ -q` |
| 数据 | 宿主 `runtime/stock_data.db`（日线+stock_basic） | 见 §3 |
| 888 湖（可选） | `C:\Users\13818\888\data_lake` 只读，用于长历史训练 | health 的 `lake.ok` |

> 本平台**不引入新依赖**：主模型用 sklearn 原生 HistGradientBoosting（LightGBM 可后续替换，接口不变）。

## 2. 部署步骤

### 2.1 校验部署

```powershell
cd E:\CODEX\Stock_selection\accumulation_breakout
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null

# 1) 自动化测试
C:\Python314\python.exe -m pytest tests/test_logic_platform/ -q      # 期望 99 passed
C:\Python314\python.exe -m pytest tests/ test_signals.py -q          # 宿主回归（约 8~16 分钟）

# 2) 数据库迁移（自动执行，幂等）
C:\Python314\python.exe -c "from logic_platform.data.ab_store import ABStore; s=ABStore(); print('schema_version:', __import__('logic_platform.data.migrations', fromlist=['schema_version']).schema_version(s.db_path))"
# 期望 schema_version = 101（logic 段）

# 3) 模型（可选，无模型时 explain.prediction=None 降级）
C:\Python314\python.exe -m logic_platform.cli.run_logic_train --codes 200 --horizon 10 --model histgb
```

### 2.2 启动服务

```powershell
# 方式 A：一键脚本（检测/启动后端 → 打开控制台；不再终止任何端口上的进程）
E:\CODEX\Stock_selection\accumulation_breakout\launch_logic_console.bat

# 方式 B：手动
set AB_BACKEND_PORT=8001
C:\Python314\python.exe web\backend_app.py  # 或 start /MIN
start "" runtime\logic_console.html
```

### 2.3 验证

```powershell
curl http://localhost:8001/api/logic/health
# 期望 {"enabled": true, "lake": {...}, "schema_version": 101, "feature_version": "v0.4.0", ...}

curl http://localhost:8001/api/logic/explain/002793.SZ
# 期望 state/box/volume/prediction/reasons 齐全
```

## 3. 数据就绪（依赖宿主既有设施）

| 数据 | 来源 | 更新命令 |
|------|------|---------|
| 日线/基础信息 | 宿主 `sync_daily.py` / `sync_history.py` | 沿用宿主惯例 |
| 训练长历史 | 宿主 SQLite（2022-08 起）优先；888 湖为备源 | `LOGIC_LAKE_ROOT` 环境变量可切湖路径 |

## 4. 目录布局（本平台相关）

```text
accumulation_breakout/
  logic_platform/                # 平台包（features/structure/dsl/prediction/backtest/data/api/cli）
  runtime/
    logic_models/vN/             # 训练模型（gitignore）
    logic_bt_result.json         # 最近闭环结果
    logic_paper_signals/YYYYMMDD/  # 纸交易观察卡
    logic_console.html           # 研究控制台（单文件，双击即开）
  docs/
    VOLUME-PRICE-LOGIC-PLATFORM.md   # 主规格
    DSL-REFERENCE.md                 # DSL 语法
    USER-GUIDE.md                    # 使用说明（本文档的配套）
    DEPLOYMENT.md                    # 部署步骤
    FINAL-ACCEPTANCE.md              # 最终验收清单
    LOGIC-PLATFORM-PHASE{0,1,2,3}-ACCEPTANCE-*.md  # 各阶段验收
  launch_logic_console.bat       # 一键启动
```

## 5. 运维要点

- **端口冲突**：8001 被占用时新路由不生效——先运行 `stop_ui.ps1` 停止本项目旧实例再重启；本项目绝不终止 8000 上的其它应用（8000 固定留给 AETF Alpha）
- **模型热更**：重训产出 `runtime/logic_models/vN+1` 即自动生效（Predictor.latest 语义版本比较）
- **日志**：后端 stdout/stderr；CLI 结构化日志含进度与闸门明细
- **回滚**：策略库/回测记录在 SQLite，删除对应行即可；模型删除目录即回退无模型模式
- **数据新鲜度**：`data_freshness.ok=false` 表示 as_of 落后——研究信号自动带警告
