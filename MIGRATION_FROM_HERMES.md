# Hermes → Grok 迁移记录

**日期**：2026-08-02  
**源**：Hermes skill + 本机运行中的选股程序  
**目标**：Grok 侧可继续审计与优化的独立工作区

## 定位到的程序

Hermes 记忆与 skill 一致指向：

| 项 | 路径 |
|----|------|
| 选股主程序 | `E:\CODEX\Stock_selection\accumulation_breakout\` |
| Hermes skill | `%LOCALAPPDATA%\hermes\skills\a-share\a-share-accumulation-breakout\` |
| 旧 Tushare 初始化 | `E:\openclaw\stock_picker_cn\tushare_http.py` |
| Grok skill（新） | `C:\Users\13818\.grok\skills\a-share-accumulation-breakout\` |

**不是** 本机其它候选（`2025stock_AI_trading`、`888/ASP`、`ashare-swing-portfolio` 等）；Hermes 文档与 cron/记忆明确绑定 **横盘吸筹→启动** 系统。

## 审计结论（迁移前）

### 健康项

| 检查 | 结果 |
|------|------|
| `test_signals.py` | 4/4 通过（正/负/趋势/真实 K 线） |
| 模块导入 | config / signals / scoring / local_store / data_fetch OK |
| 本地库 | daily 216 万行；moneyflow 204 万行；stock_basic 5537 |
| scan_result | 20 条（as_of `20260731`） |
| 前端 `tsc -b` | 通过 |

### 风险与技术债

1. **数据滞后**：库内最新日 `20260731`，相对日历日偏旧；交付前必须 `sync_daily` + 重扫。
2. **Token 硬编码**：旧 `E:\openclaw\...\tushare_http.py` 写死 token；迁移后改为 env / `.env` + 旧文件回退。
3. **外部路径耦合**：`data_fetch` 曾只依赖 openclaw；已改为优先本目录 `tushare_http.py`。
4. **SQLite WAL 体积大**：`stock_data.db` ~0.97GB + wal ~130MB，勿整库复制到 C:。
5. **测试面窄**：仅信号引擎；缺 scoring / API / store 单元测试。
6. **Hermes 环境坑**：`PYTHONPATH` 与本机代理会污染 Python 3.14 — 脚本内已 pop，运行前仍建议清环境。

## 已完成的迁移动作

1. 在项目内 **vendor** `tushare_http.py`（env 优先，legacy 回退）。
2. 更新 `data_fetch.py` 导入路径优先级。
3. 新增 `AGENTS.md` / `README.md` / `requirements.txt` / `.env.example`。
4. 将 Hermes skill + references 同步到 `~/.grok/skills/a-share-accumulation-breakout/`。
5. 本文件记录审计与 backlog。

## 未做（有意保留）

- **未搬迁** `runtime/stock_data.db` 与 `out/cache/*.pkl`（体积大，路径仍在 E:）。
- **未删除** Hermes skill（可双轨；Grok 侧以本仓库 + `~/.grok/skills` 为准）。
- **未改策略参数**（优化留作下一步）。

## 下一步优化建议（优先级）

1. P0：增量同步到最新交易日 → 重跑 `run_screener` → 核对 as_of。
2. P0：配置 `.env` 中 `TUSHARE_TOKEN`，去掉对 openclaw 硬编码的依赖。
3. P1：pytest 覆盖 scoring / LocalStore upsert / scan 空结果 API。
4. P1：UI 展示数据日期与“是否过期”徽章。
5. P2：参数网格 + 简单样本外验证，防过拟合。
