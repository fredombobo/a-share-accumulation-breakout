# 横盘吸筹 → 启动 选股系统

A 股「横盘吸筹平台 + 放量突破」筛选工具（本地运行）。  
**研究辅助，不是投资建议。**

## 当前定位：个人选股与回测学习工作台

日常只保留「每日选股 → 多参数专业回测 → 查看报告 → 自主决定是否复用参数」主线。
今日选股允许手动配置，不要求先通过回测；回测参数也可以带回选股，但不代表已经证明有效。
纸面交易不在默认产品流程内，系统不会向券商下单。

- 分层抽样覆盖沪深市场，可按当前行业/板块或明确股票列表选择研究范围。
- 横盘长度、突破量比及进出场条件支持网格步进，保留止盈、止损和筹码条件扩展接口。
- 报告比较样本内、样本外、滚动窗口、随机/均线基线及双倍成本。
- 新报告展示入选参数的实际模拟账户曲线、月度损益、逐笔费用与已实现盈亏贡献；旧报告不补造明细。
- 行情浏览范围与可验证研究范围分别展示；历史数据修订按当前冻结知识快照回放，**不等于取得历史当时的完整数据版本或行业成员**。当前股票池仍有幸存者偏差限制。

发布/验收：[个人研究收束记录](docs/PERSONAL-RESEARCH-CLOSEOUT-2026-09-05.md)。
对外介绍边界：[小红书功能清单与表述说明](docs/PERSONAL-RESEARCH-XHS-2026-09-05.md)。
已有研究 FAIL / 证据不足结论保留；工程验收通过不代表策略可稳定盈利。

> ✅ **UI 入口（唯一）**：单端口 UI `http://127.0.0.1:8001/`
> （`bootstrap.py` / `一键启动.bat` 启动，后端已托管前端）。旧版 Streamlit `app.py` 已于 2026-08-03 移除。
> 端口 8000 固定留给其它应用（AETF Alpha），请勿在本项目使用。

---

## 给 AI Agent（推荐：用户只提供 Token）

用户只需给出 **Tushare Token**。Agent 执行：

```bash
git clone https://github.com/fredombobo/a-share-accumulation-breakout.git
cd a-share-accumulation-breakout
python bootstrap.py --token "<TUSHARE_TOKEN>" --yes --no-browser
```

成功标志：终端出现 `BOOTSTRAP_OK url=http://127.0.0.1:8001/`  
然后让用户打开该地址，点 **「扫描」**，约 5～15 分钟后看 **A 池**。

- 给 Agent 的完整提示词：[PROMPT_FOR_AGENT.md](./PROMPT_FOR_AGENT.md)  
- Agent 协议说明：[FOR_AGENTS.md](./FOR_AGENTS.md)

```powershell
# Windows 等价
$env:TUSHARE_TOKEN = "<TUSHARE_TOKEN>"
python bootstrap.py --yes --no-browser
```

---

## 小白最短路径（自己点鼠标）

### 只要 3 步

1. 安装 [Python 3.12](https://www.python.org/downloads/)（当前验收版本；勾选 **Add to PATH**）
2. **双击 `一键启动.bat`** → 按提示粘贴 [Tushare Token](https://tushare.pro)  
3. 浏览器打开后点 **「扫描」**，等结果看 **A 池**

停止：双击 **`停止.bat`**

更细的说明 → [docs/小白使用手册.md](./docs/小白使用手册.md)

### 耗时心里有数

| 动作 | 大约时间 |
|------|----------|
| 日常同步行情 | **2～10 分钟** |
| 第一次建库 | **30～90 分钟+** |
| 全市场扫描 | **5～15 分钟** |
| 打开网页 | **约 30 秒** |

---

## 界面里怎么用

| 你看到的 | 含义 |
|----------|------|
| **A 池** | 严格条件，优先看这些 |
| **B 池** | 放宽/观察，不要当主推 |
| **防守** 且 A 池空 | 市场弱，系统禁止新开仓，**正常** |
| 数据日期很旧 | 再跑一次一键启动做同步 |

单端口地址：`http://127.0.0.1:8001/`（后端已托管前端，不必再开 npm）

---

## 策略摘要

1. **专业箱体**：1～6 个月；稳健振幅 + 支撑/压力触及 + 拒单边通道  
2. **突破**：收盘有效突破 + 放量 + 涨幅适中 + 站稳 + 均线  
3. **A/B 分池**，主题软加分，多核扫描  
4. 交易卡片：止损 / 目标 / 建议仓位  

---

## 进阶命令（可跳过）

```powershell
copy .env.example .env   # 填 Token
python -m pip install -r requirements.txt
python easy_start.py     # 等同双击一键启动
python sync_daily.py
python run_screener.py --top 15 --days 160 --workers 0
python test_signals.py
```

`sync_daily.py` 会增量补齐个股日线、日指标、资金流和研究/门禁共用的
`000300.SH` 基准指数；新行情统一写入来源、可用时点和抓取时点。基准接口发生
瞬时断连时会有限重试，超过次数后仍明确失败，不会把缺失数据当作同步成功。

### 日用产品边界

8001 发布产品只保留 **每日选股、个股详情、专业回测**。旧单参数实验室、纸面交易和机构控制台
均已退出发布面，避免把不成熟功能混进每天使用的闭环。

专业回测支持固定值、范围步进和离散值网格；按板块或股票冻结研究池，输出净成本、IS/OOS、
WF、随机/均线基线和 2 倍成本压力。网格结论始终是探索性证据，不会自动进入 A 池或生产参数。
个股 AI 评测默认只读本地证据，外部模型必须手工触发，失败时不影响本地评测。
研究定义与历史证据见 `docs/RESEARCH-ROADMAP.md`、`docs/ENTRY-DEFINITION-V1.md` 和历史验收文档。

开发前端：

```powershell
cd web\frontend
npm install
npm run build          # 更新 dist 后仍可用单端口
# 或 npm run dev → :3001
```

---

## 注意

- Token 只放 `.env`，**不要提交、不要发给别人**  
- 本地行情库在 `runtime/`（已 gitignore）  
- 防守环境 A 池为空是风控，不是程序坏了  

## 目录要点

```
一键启动.bat / 停止.bat / easy_start.py   ← 小白入口
docs/小白使用手册.md
signals.py / parallel_scan.py / run_screener.py
web/backend_app.py + web/frontend/dist    ← 单端口 UI
```
