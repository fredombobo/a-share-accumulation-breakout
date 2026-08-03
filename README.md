# 横盘吸筹 → 启动 选股系统

A 股技术形态选股：识别**横盘吸筹平台**后**放量突破启动**的标的，结合资金流与基本面综合打分。

> 研究/辅助工具，**不是投资建议**。实盘请自行风控。

## 策略摘要（胜率优先）

1. **专业箱体**：1~6 个月（20~125 交易日）；稳健振幅 + 支撑/压力触及 + 摆动结构 + 拒单边通道
2. **突破**：近 5 日收盘有效突破阻力 + 放量 + 涨幅 2%–9.5% + 站稳 + MA 确认
3. **A 池（可交易）**：仅 `strict` + 资金流质量 + 新鲜度；默认 Top **15**
4. **B 池（观察）**：`relaxed` / `theme_fill`，**禁止与 A 混排**
5. **环境**：进攻 / 中性 / 防守（防守期 A 池清空，禁止新开仓）
6. **交易卡片**：止损 / 目标 / 建议仓位 / 最长持有约 15 日
7. **多核扫描**：`parallel_scan.py`，默认 `cpu_count-1` 进程

## 耗时量级（本机约 6 核，仅供参考）

| 步骤 | 大约时间 |
|------|----------|
| 日常增量同步 `sync_daily.py` | **2～10 分钟**（缺 1～数日数据） |
| 首次建库 / 大补洞 | **30～90+ 分钟**（视网络与历史跨度） |
| 全市场扫描 `run_screener.py`（本地库已就绪） | **5～15 分钟**（多核；含 strict+relaxed） |
| 启动 Web UI | **约 30 秒** |

## 快速开始

```powershell
cd accumulation_breakout
copy .env.example .env   # 填入 TUSHARE_TOKEN
python -m pip install -r requirements.txt

python sync_daily.py
python run_screener.py --top 15 --days 160 --workers 0
python test_signals.py
python test_parallel_scan.py
```

### Web UI（推荐一键）

```powershell
.\start_ui.ps1          # 后端 :8000 + 前端 :3001
# 浏览器 http://127.0.0.1:3001/
.\stop_ui.ps1
```

功能：A/B 池 · 数据过期标红 · 市场环境 · 交易卡片 · 持仓止损页

## 目录

```
accumulation_breakout/
├── config.py / signals.py / scoring.py / charting.py
├── data_fetch.py / local_store.py / sync_daily.py
├── parallel_scan.py         # 多进程扫描
├── tushare_http.py
├── run_screener.py
├── web/                     # FastAPI + React
├── runtime/                 # 本地库（不入库）
└── out/                     # 导出（不入库）
```

## 注意

- Token 放 `.env`，**不要提交**
- 推荐前核对数据 as_of / 交易日新鲜度
- 防守环境结果为空是预期行为，不是程序坏了

## 迁移说明

见 [MIGRATION_FROM_HERMES.md](./MIGRATION_FROM_HERMES.md) 与 [AGENTS.md](./AGENTS.md)。
