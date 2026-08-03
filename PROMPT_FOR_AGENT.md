# 复制给任意 AI Agent 的启动提示词

把下面整段发给 Agent（**把 Token 换成用户的**）：

---

请帮我部署并启动开源项目「A 股横盘吸筹→启动选股系统」。

## 仓库

https://github.com/fredombobo/a-share-accumulation-breakout

## 我的唯一凭证

```text
TUSHARE_TOKEN=在这里粘贴用户的token
```

## 你必须执行的命令（不要省略）

```bash
git clone https://github.com/fredombobo/a-share-accumulation-breakout.git
cd a-share-accumulation-breakout
python -m pip install -U pip
python bootstrap.py --token "$TUSHARE_TOKEN" --yes --no-browser
```

Windows PowerShell 可用：

```powershell
git clone https://github.com/fredombobo/a-share-accumulation-breakout.git
cd a-share-accumulation-breakout
$env:TUSHARE_TOKEN = "在这里粘贴用户的token"
python bootstrap.py --yes --no-browser
```

## 成功标准

1. 终端出现 `BOOTSTRAP_OK url=http://127.0.0.1:8000/`
2. `http://127.0.0.1:8000/api/health` 返回 JSON 且 status 正常
3. 告诉我：打开 http://127.0.0.1:8000/ ，点「扫描」，约 5～15 分钟后看 **A 池**

## 约束

- 不要要求我手动装一堆东西；依赖用 `bootstrap.py` 自动装
- 不要把 Token 写进公开日志/README/commit
- 首次同步可能要 30～90 分钟，属正常；可先启动界面
- 若 A 池为空且提示「防守」，是风控不是故障

---

## 更短版（一行给 Agent）

```text
克隆 https://github.com/fredombobo/a-share-accumulation-breakout ，执行 python bootstrap.py --token <我的TUSHARE_TOKEN> --yes ，成功后打开 http://127.0.0.1:8000/ 并说明点「扫描」看 A 池。Token 勿泄露。
```
