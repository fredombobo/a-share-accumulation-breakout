# 阶段0 验收报告：修复现有阻断问题

日期：2026-08-07
状态：✅ 全部通过

## 修复内容

### 1. parallel_scan.py 看门狗误杀父进程风险
- **问题**：`_watch_parent_or_exit` 用 `os.kill(ppid, 0)` 探测父进程存活。Windows 上对受保护进程会抛 `PermissionError`（OSError 子类），被 `except OSError` 一律当作"父死"，导致活着的 worker 自杀；旧版 CPython 甚至将 sig=0 实现为 `TerminateProcess` 直接杀死父进程。
- **修复**：新增 `_parent_alive(ppid)` 安全探测——psutil 优先（`is_running()` + 非 zombie），Windows 兜底用 `ctypes.OpenProcess + GetExitCodeProcess`（完全不触碰 TerminateProcess），POSIX 兜底 `os.kill(ppid, 0)` 但只把 `ProcessLookupError` 当父死。只对**确定性不存在**的 PID 判定死亡。
- **测试**：`test_parent_alive_probe_safe`、`test_full_scan_does_not_kill_pytest`、`test_cancel_scan_returns_promptly`

### 2. 后端构建版本 + 启动时间 + 启动器自动重启
- **新增** `build_version.py`：轻量模块（无第三方依赖），计算后端源码 + 前端 dist 指纹（SHA-256）。
- **后端** `/api/health` 新增 `build_version`、`started_at` 字段。
- **启动器** `easy_start.py`：服务已在运行时比对本地指纹与运行中版本，不一致（源码/前端产物更新）→ 自动停止旧后端并重启加载新版本。

### 3. 详情页"近5日资金流"实际累计全部历史
- **问题**：`stock_detail` 注释写"近5日"，实际把全部历史 moneyflow 传入 `calc_fund_flow_strength` 计算。
- **修复**：`scoring.calc_fund_flow_strength` 新增 `days` 参数 + `_tail_trading_days` 按交易日截断；`stock_detail` 传 `days=5`，`fund_flow.days` 固定返回 5。
- **测试**：`test_fund_flow_strength_last_5_days_only`（全部历史为正、近5日为负，精确等于 -500×5）。

### 4. 总览轻量列表 vs 详情数据拆分
- **问题**：`/api/overview` 每个候选返回**全量 K 线 + 财务数据**，且逐只串行重算信号，30 只冷请求 25s、响应 >1MB。
- **修复**：
  - 移除 overview 的 `fina` 字段（财务详情走 `/api/stock/{ts_code}`，前端总览页未使用）
  - kline 只返回最近 60 条（SQL 层截断 + 批量加载一次按 code 分组）
  - 信号字段 `box_high/box_low/ma5/ma20` **持久化到 scan_result 表**（扫描时写入，`backfill_scan_signals.py` 回填存量），overview 直接读表零重算；仅 `sig_calculated=0` 的缺失行才触发并行/串行重算（<5 只串行避免 spawn 开销）
  - 新增 `_OVERVIEW_CACHE` 按 (数据日期, 池) 缓存的轻量列表，扫描完成后自动失效
  - `detect_many` 新增 `min_codes_for_pool` 参数（小样本也可强制多进程）

## 验收结果

| 验收项 | 判定规则 | 实测 | 结果 |
|---|---|---|---|
| 完整测试运行不杀死 pytest 进程 | 扫描测试后 `os.getpid()` 不变 | 81/81 通过 | ✅ |
| 取消扫描与父进程退出测试 | 新增测试通过 | 3 项新增 | ✅ |
| 前端引用的所有接口在 OpenAPI 中 | 逐条比对 | 全部对应 | ✅ |
| 资金流固定数据集只累计最近 5 个交易日 | days=5 + 回归测试 | -500×5 精确 | ✅ |
| 30 个候选总览响应 < 300KB | 实测字节数 | 227.4 KB | ✅ |
| 本机热请求 < 1 秒 | 连续请求均值 | 0.14~0.24 s | ✅ |
| 本机冷请求 < 2.5 秒 | 服务重启后首请求 | 1.28~1.80 s | ✅ |

## 新增/修改文件

| 文件 | 改动 |
|---|---|
| `parallel_scan.py` | `_parent_alive` 安全探测、`min_codes_for_pool` 参数 |
| `build_version.py` | **新增**：构建指纹模块 |
| `web/backend_app.py` | health 版本字段、overview 轻量化 + 缓存、资金流 days=5、信号持久化读取 |
| `scoring.py` | `calc_fund_flow_strength(days=)` + `_tail_trading_days` |
| `local_store.py` | scan_result 表新增 `box_high/box_low/ma5/ma20/sig_calculated` 列 + 增量迁移 |
| `run_screener.py` | 扫描时持久化信号字段 + `sig_calculated=1` |
| `easy_start.py` | 版本检测 + 自动重启 |
| `backfill_scan_signals.py` | **新增**：存量信号字段回填（41 行） |
| `test_parallel_scan.py` | 新增 3 项测试 |
| `test_phase4.py` | 新增资金流近5日回归测试 |

## 备注
- 扫描完成后 overview 缓存自动失效（`_OVERVIEW_CACHE` 清空）。
- 下次运行 `run_screener.py` 扫描时新数据自动携带信号字段，无需再回填。
- 2 只无箱体股票（box_high=NULL）属合法状态（`is_breakout=False` 无箱体），已用 `sig_calculated=1` 标记避免每次重算。
