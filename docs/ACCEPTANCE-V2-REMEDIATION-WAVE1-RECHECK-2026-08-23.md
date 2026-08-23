# v2.0 纠错工程 Wave 1 第二轮独立验收

- 验收时间：2026-08-23 15:45 Asia/Shanghai
- 固定基线：`b6772c3001e1fa37447fca813b7ad3512b54eb49`
- 管理结论：`REWORK_REQUIRED`
- 已接受：`V2R-Q1`、`V2R-A`（沿用首轮结论）
- 仍需返工：`V2R-D`、`V2R-X`、`V2R-F`、`V2R-O1`
- Wave 2：不释放；`V2R-S`、`V2R-O2`、`V2R-R`、`V2R-N` 继续 blocked
- 安全结论：未合并返工分支，未写生产数据库，未修改或删除备份，未开启真实交易、PIT 正式读、v2 执行写、风险 enforce 或日调度。

## 1. 总结

四个实现都修复了部分表面问题，但都没有完整关闭首轮缺陷。定向 Pytest、Ruff、Mypy 或 TypeScript build 通过，不等于合同级验收通过；本轮额外执行了小样本、异常降级、锁文件、E2E 服务身份、恢复目标与 OpenAPI 等边界复现。

| 任务 | 二验 head | 已确认修复 | 仍阻断 |
|---|---|---|---|
| V2R-D | `57c5a8b764cb` | 默认自动采样不足时返回 INSUFFICIENT | 显式小样本仍可 PASS；报告身份仍是基线 SHA；公司行为仍可 0 标的跳过 |
| V2R-X | `d7551bc310a3` | resolved enforce flag 已接线，enforce 内部异常返回 RISK_UNAVAILABLE | observe 异常仍直接抛出并被调用方吞掉；无明确降级检查，调用边界仍非完整 fail-closed |
| V2R-F | `f5bfff4fd9f9` | 推荐预设 step=5；健康响应改为 nested 类型；增加 webServer 声明 | lockfile 不同步、Vitest 0 测试、E2E 因固定 3001 端口失败，状态测试无法验真 |
| V2R-O1 | `b02ed05d5a2` | HTTP 不再公开 backup_root；DryRun 原命令变为 exit 0 | 未推导安全临时目标；SQL 禁调用测试仍未实现 |

## 2. V2R-D

判定：`REWORK_REQUIRED`。

新鲜证据：

- 定向 Pytest：`24 passed in 26.23s`。
- Ruff：0；Mypy：0。
- 既有显式 3 标的 × 1 日期测试仍得到 `PASS`；这直接证明最小样本硬门只作用于 `codes is None and dates is None`，调用方显式传小样本时仍可假通过。
- 现有副本产物 `E:/ab-maintenance/v2r-d/reports/shadow_parity.json` 为 `PASS / 100 samples / 600 pairs`，但 `code_sha=b6772c3001e1`；当前 head 为 `57c5a8b764cb...`，身份不一致。
- `real_data_gate.py` 只有在 `seed_codes` 非空时才选一个公司行为探测代码；两者都为空时 `corporate_action_codes_checked=0` 且没有追加 issue。

必须修复：

1. `V2R-D-RW-001`：不区分默认或显式参数；少于 20 标的、5 日期、100 样本或 600 字段比较，一律不得 PASS。旧的 3×1 成功测试应改成足量 fixture，另加显式不足测试。
2. `V2R-D-RW-002`：在修复后 head 上重生不可变 parity 报告；核对 `code_sha`、config hash、DB fingerprint、报告 SHA-256 与实际文件。
3. `V2R-D-RW-003`：无持仓时从有效行情/active universe 选择至少一个探测标的；仍无法选择则 FAIL/INSUFFICIENT，且测试断言 `corporate_action_codes_checked >= 1` 或明确失败。

## 3. V2R-X

判定：`REWORK_REQUIRED`。

新鲜证据：

- 定向 Pytest：`44 passed in 18.63s`。
- Ruff：0；Mypy：0。
- enforce flag 和内部 `build_portfolio_state` 异常路径已经修复，可关闭 `V2R-X-RW-001`。
- 合同复现把 observe 模式的风险后端置为不可用，实际输出 `OBSERVE_EXCEPTION=RuntimeError:risk backend down`。`review_order` 与 `confirm_order` 的外围 `except Exception: pass` 仍会无条件吞掉该异常，因此没有“风险降级”检查；若统一入口在内部 try 之外失败，enforce 调用边界仍可继续。

必须修复 `V2R-X-RW-002`：统一入口应始终返回结构化结果，至少包含 `blocked/mode/violations/degraded`；observe 异常返回明确的 `RISK_UNAVAILABLE` 降级检查，enforce 异常 fail-closed。review 和 confirm 必须新增 monkeypatch“统一入口直接抛异常”的双态测试，禁止裸 `except Exception: pass`。

## 4. V2R-F

判定：`REWORK_REQUIRED`。

新鲜证据：

- `npm ci --dry-run --ignore-scripts`：exit 1，`package.json` 与 `package-lock.json` 不同步；缺少 Playwright、Vitest、Testing Library、jsdom 及传递依赖。
- `npm run test`：exit 1，缺 `http-proxy-agent`，`Test Files: no tests / Tests: no tests`。
- `npm run build`：exit 0；推荐预设已发送 `step: 5`，健康类型已按 nested 后端响应调整。
- `npm run test:e2e`：exit 1；Playwright 因 `http://127.0.0.1:3001` 已占用拒绝启动。`reuseExistingServer=false` 避免误测旧应用是正确方向，但固定占用端口仍使当前分支无法验收。

必须修复：

1. `V2R-F-RW-001`：同步并提交 lockfile；在干净依赖目录执行 `npm ci` 后，Vitest 必须实际收集并通过测试。
2. `V2R-F-RW-002`：E2E 使用独立验收端口（例如 preview 4174）或可靠的动态端口，baseURL 与 webServer 一致，禁止复用 3001 上未知服务；四个用例全部通过。
3. `V2R-F-RW-004`：实现代码方向正确，但加载、空、错误、证据不足、正常状态及响应 fixture 仍必须由可运行测试证明；在测试门恢复前不关闭此缺陷。

## 5. V2R-O1

判定：`REWORK_REQUIRED`。

新鲜证据：

- 定向 Pytest：`9 passed in 1.79s`；Ruff 0；Mypy 0。
- 合同原命令现 exit 0，但打印 `restore target: (unspecified)`，没有按合同推导安全临时恢复目标。
- OpenAPI 实测：health 参数仅 `port`，backups 无参数；`V2R-O1-RW-002` 可关闭。
- 对 16,324,935,680 字节生产数据库执行只读快速健康为 `3.545ms`，性能符合 `<500ms`；结果诚实为 FAIL，深检证书 MISSING。
- `test_fast_health_never_runs_full_integrity_check` 仍仅断言响应中没有 `integrity` 字段，没有 monkeypatch/capture `sqlite3.Connection.execute`；无法证明热路径没有执行 `PRAGMA integrity_check` 或 `quick_check`。

必须修复：

1. `V2R-O1-RW-001`：DryRun 未传 RestoreTo 时生成并打印绝对的安全临时目标；验证该目标不等于生产 DB、不存在覆盖路径，且不执行复制。
2. `V2R-O1-RW-003`：用连接代理/trace callback 捕获所有 SQL；发现 `integrity_check` 或 `quick_check` 立即失败。保留大库性能证据。

## 6. 复验命令

V2R-D、V2R-X、V2R-O1 继续执行首轮报告中的 Pytest/Ruff/Mypy 命令，并额外运行本报告列出的合同边界测试。V2R-F 必须按以下顺序在干净依赖环境执行：

~~~powershell
npm --prefix web/frontend ci
npm --prefix web/frontend run test
npm --prefix web/frontend run build
npm --prefix web/frontend run test:e2e
~~~

任何步骤非零、0 测试、命中未知旧服务或缺少产物身份，都不得自报通过。

## 7. 当前事实边界

本次二验不改变生产与研究结论。权威运行证据仍是旧证据：`latest_run_evidence.json` 生成于 2026-07-18；机构分 86，anti-overfit 与 strict anti-overfit 均未通过；商业复核 88，低于 94 通过线；持仓同步仍为 `stale_local_cache`（2026-08-19）。因此整体状态继续为 `BLOCKED`，不得宣称 9 分、机构级完成、研究晋级通过或真实交易就绪。

## 8. 下一轮提交要求

- 四个任务继续在原分支追加最小修复提交，不重写历史，不提交 `web/frontend/dist`。
- handoff 必须逐个引用剩余 defect ID，并附修改前失败、修改后成功的原始输出。
- 管理者只有在四项全部 ACCEPTED 后才释放 Wave 2；本轮不做跨分支集成合并。
