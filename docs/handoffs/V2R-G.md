# V2R-G 共享入口、服务端 flags 与 readiness 收口

日期：2026-08-27  
项目：`accumulation_breakout`（Breakout，端口 8001；未修改 AETF/8000）

## 结论

G 工程实现与契约验收通过，可以进入管理者 P8 独立总验收；这不表示七闸门已通过。
平台仍无真实券商能力，`LIVE_TRADING_ENABLED=false`。权威研究 FAIL、当前身份真实数据
门禁和五个真实完成交易日等阻断继续原样保留。

## 实现结果

- 修正 readiness 优先级：dirty 或 identity mismatch 永远先判 `BLOCKED`；只有身份干净且
  除 R 外全部 PASS 才是 `ENGINEERING_READY_RESEARCH_BLOCKED`；七门全 PASS 才返回
  `PERSONAL_INSTITUTIONAL_READY`。
- 新增永久可读：
  - `GET /api/v2/platform/status`
  - `GET /api/v2/readiness`
- readiness 不接收客户端门禁布尔值：D 读取真实数据门禁，R 固定读取权威任务
  `0746a4108e15`，O 读取备份、当前身份五日 soak 与严格恢复证据；S/P/L/G 读取
  `runtime/v2/gates` 中经 SHA-256 和当前 git/build/config/db 身份校验的文件。
- 服务端解析 flags 一次并执行；关闭的 V2 业务返回 HTTP 503 + `FEATURE_DISABLED`。
  资金、份额、T+1、不做空、PIT、对账和 `LIVE=false` 是不可关闭硬门。
- 自动纸面日结线程现在同时要求 `PAPER_TRADING_ENABLED` 和
  `DAILY_SCHEDULER_ENABLED`；当前后者为 false，不在五日观察前擅自开启。
- 每个 v2 响应携带 `X-AB-Version` 与 `X-AB-Product=accumulation_breakout`；系统健康
  返回真实 build/config hash。
- 修复全部 V2 前端请求的双前缀缺陷：`/api/api/v2/*` → `/api/v2/*`，并加入回归测试。
- 侧栏只依据服务端 status 展示 V2 控制台；策略/信号在生产观察落库 flag 关闭时明确
  标“只读”，不能由 URL 或 localStorage 越权开启生产行为。
- 构建版本从 `size+mtime` 改为内容 SHA-256；同一内容跨 worktree/checkout 保持同一身份，
  内容变化必然变号。resolved config hash 同样排除 checkout 绝对路径。
- 更新 Breakout 项目指令中的权威路径、Python 3.12、8001/8000 隔离和纸面账本事实源。

## 接口与配置身份

| 项 | 值 |
|---|---|
| 产品 | `accumulation_breakout` |
| 默认端口 | `8001` |
| 最终前端 asset 引用 | 3 个，缺失 0 |
| 本次构建版本（构建后） | `d37a217893ed` |
| 前端指纹 | `9c8d3437614b9df0` |
| resolved platform config hash | `85f6c3be1d0d22aa` |
| 控制台 flag | true |
| 策略生产观察落库 | false（目录只读可见） |
| PIT 正式读 / execution write / risk enforce / scheduler | false |
| 实盘 | false（硬门） |

以上构建号会在本提交写入后因新增 handoff 内容是否进入源码遍历范围而复算；P8 必须以最终
clean RC commit 现场接口返回值为准，不能抄用本表替代现场身份。

## 验收证据

使用 `E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe`：

| 门禁 | 结果 |
|---|---|
| readiness/platform/OpenAPI 定向 | 24 passed |
| `python -m pytest -q` | **885 passed in 253.34s** |
| `python -m pytest -q -m performance` | **3 passed, 882 deselected** |
| `python -m pytest -q -m fault_injection` | **18 passed, 867 deselected** |
| Ruff 全仓 | PASS |
| Mypy | PASS，241 source files |
| strict architecture | PASS |
| Vitest | 7 passed |
| TypeScript + Vite build | PASS，700 modules |
| dist hashed assets | index 引用 3，缺失 0 |
| `git diff --check` | PASS |

Vite 仍提示 ECharts 独立 chunk 687.38KB（gzip 230.08KB）。该图表 vendor 已独立分块，
不阻断功能和首屏主 chunk（234.14KB）；P8 性能报告应继续记录该已知前端容量项。

## 数据与安全

- 所有自动测试使用临时迁移库；本任务没有迁移、写入或修改 16GB 生产数据库。
- 没有读取、打印或提交 Tushare Token。
- 没有开启实盘、执行新核心写账、风险 enforce、PIT 正式读或自动 scheduler。
- dist 由当前 Breakout 源码统一构建，不包含 AETF 名称或 8000 端口身份。

## P8 必须诚实处理的阻断

1. 最终 clean RC 身份上重跑真实数据门禁；旧报告不能复用。
2. 权威研究任务 `0746a4108e15` 当前为 FAIL；如 final build 身份不同还需重跑权威研究。
3. 当前身份五个真实完成交易日 soak 不足，不能回填或压缩等待。
4. 必须有至少七份异位置备份与一次严格恢复演练证据。
5. 自定义 Tushare 明文 HTTP 通道在可信 TLS 隧道前必须保持安全阻断，不能全绿。

## 回滚

- 源码与配置按 G 提交整体 `git revert`；无数据库回滚。
- 前端 dist 与源码在同一提交回退，避免哈希资源错配。
- 关闭 `INSTITUTIONAL_CONSOLE_V2_ENABLED` 可隐藏并服务端阻断控制台业务；
  `platform/status`、`readiness`、系统健康仍可用于诊断。

本 handoff 仅声明 G 工程验收通过，未声明 `PERSONAL_INSTITUTIONAL_READY`。
