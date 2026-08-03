# 横盘吸筹 → 启动 选股系统

A 股「横盘吸筹平台 + 放量突破」筛选工具（本地运行）。  
**研究辅助，不是投资建议。**

---

## 给 AI Agent（推荐：用户只提供 Token）

用户只需给出 **Tushare Token**。Agent 执行：

```bash
git clone https://github.com/fredombobo/a-share-accumulation-breakout.git
cd a-share-accumulation-breakout
python bootstrap.py --token "<TUSHARE_TOKEN>" --yes --no-browser
```

成功标志：终端出现 `BOOTSTRAP_OK url=http://127.0.0.1:8000/`  
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

1. 安装 [Python 3.11+](https://www.python.org/downloads/)（勾选 **Add to PATH**）  
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

单端口地址：`http://127.0.0.1:8000/`（后端已托管前端，不必再开 npm）

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
