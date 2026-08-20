# 个人研究路线图：A 股「平台突破」形态

> 更新：2026-08-06  
> 根目录：以本机 `accumulation_breakout` 实际路径为准（常见 `C:\Users\13818\accumulation_breakout`）

## 目标边界

- **要做**：形态扫描、参数摸底、IS/OOS/WF 纪律、环境过滤下的 A 池研究辅助  
- **不做（本阶段）**：券商对接、自动下单、把 Lab 排行榜当买卖指令  

## 相关扩展规格

- **量价预测 · 逻辑生成平台**（挂本仓 + 复用 888 数据湖）：[`VOLUME-PRICE-LOGIC-PLATFORM.md`](./VOLUME-PRICE-LOGIC-PLATFORM.md)

## 两区隔离（强制心智模型）

| 区域 | 入口 | 用途 | 可否当「明天买谁」 |
|------|------|------|-------------------|
| **可交易研究** | UI 总览 `/` · A 池 | 当日/近窗形态 + 防守过滤 | 仅作候选，仍需人工 |
| **参数研究** | UI `/lab` · CLI 优化 | 历史网格 / OOS / 擂台 | **否** |

## 推进顺序

### P0 — 数据与验证窗

1. 有效 `TUSHARE_TOKEN` 写入 `.env`  
2. `python research_status.py` → 看 mode / 下一步  
3. `python sync_history.py` → 目标 ~730 交易日（断点续传，2~4h）  
4. 再次 `research_status.py` → **mode=full** 后才严肃谈 edge  

当前库若只有 ~400 日（约 2024-12 起），系统自动 **degraded** 窗（约 65% IS / 35% OOS），结果**仅摸底**。
**2026-08-08 本机快照：日线 ~969 日 → mode=full**（edge 仍需净成本门禁，非自动通过）。

### P0b — 入场定义与证据（2026-08-08 启动）

| 项 | 状态 | 入口 |
|----|------|------|
| A 池入场定义 v1 冻结 | ✅ | `docs/ENTRY-DEFINITION-V1.md` · `ab_screener/domain/entry_definition.py` |
| 回测/trade_sim 对齐次日开盘 | ✅ | `backtest_signals.py` · `trade_sim.py` |
| 假突破归因 5/10/20 | ✅ | `python run_attribution.py` |
| 成本后 IS/OOS 证据报告 | ✅ | `python run_evidence_report.py` |
| 双基线自动对比写入证据包 | ⏳ | 现 `beats_baseline` 默认 fail-closed |
| 状态看板 | ✅ | `docs/STATUS.md` |

### P1 — 文档与入口一致

- Agent/小白/研究三条入口不互相踩  
- 路径、Lab 已上线状态、Token 要求与代码同步  

### P2 — 任务持久化

- ✅ 扫描：`scan_jobs` 表（`ab_screener/application/scan_jobs.py`）
- ✅ Lab：`research_runs` 表（`ab_screener/research/store.py`）
- ⏳ 进程崩溃后 Web 内存任务与 DB 完全对齐的 UX 文案

### P3 — UI 隔离（已启动）

- 侧栏标注「总览=A池 / 实验室=研究」  
- Lab 顶栏展示研究模式与「非下单」说明  

### P4 — CI 烟雾（稍后）

- pytest + tsc + health/lab 烟雾

### P5 — 机构级控制台升级（待执行）

完整 Agent 可执行计划（指挥舱 / 方案库 / 监控预警 / 组合约束 / 复盘 / 平台硬化）：

`docs/superpowers/plans/2026-08-11-institutional-console-upgrade.md`  

## 每日研究工作流（推荐）

```powershell
cd <本项目根>
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null

python research_status.py          # 先看数据与 Token
python sync_daily.py               # 日常补最新交易日
# 浏览器 http://127.0.0.1:8001/  → 扫描 → 只看 A 池 + 环境
# 参数摸底（mode≠insufficient 时）：
python run_optimize_plan.py A 200 15
python research_status.py          # 确认仍是 degraded/full
```

## 何时可以说「参数可能有 edge」

同时满足：

1. `research_status` → `mode=full` 且 `can_claim_edge=true`  
2. OOS 胜率 / PF / 回撤过过滤  
3. WF 至少部分窗口 `wf_pass`  
4. 与「扫描 A 池」逻辑一致的入场定义（无偷换标签）  

否则只写：**摸底观察，样本不足或未严格样本外**。

## 相关命令

| 命令 | 作用 |
|------|------|
| `python research_status.py` | 研究就绪报告 |
| `python sync_history.py` | 历史扩容 |
| `python run_attribution.py` | 假突破归因（ENTRY v1） |
| `python run_evidence_report.py` | 成本后 IS/OOS 证据包 |
| `python run_optimize_plan.py A 600 10` | 自动窗 IS/OOS |
| `python pipeline_seed.py A 600 10` | WF + 播种 + 擂台干跑 |
| `GET /api/lab/research-status` | UI/Agent 读就绪状态 |
