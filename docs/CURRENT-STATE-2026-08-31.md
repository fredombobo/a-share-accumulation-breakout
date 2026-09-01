# 当前状态与收口记录（2026-08-31）

> 本文件记录 2026-08-31 的收口过程与结果。
> 不覆盖 `docs/STATUS.md`（该文件按约定由用户维护）。
> **工程状态 ≠ 研究结论。本文件不构成任何 edge、可跟单或收益承诺。**

## 0. 结论

代码分裂已收口。`origin/main` 从 `2c04962`（08-21）快进到 `b3f4d58`，
全量测试 **1144 passed / 0 failed**，前端构建通过。

剩余的全部阻断都在**研究门禁**，不是工程债——其中一条（闸门 R）按定义无法通过工程手段解决。

> 2026-09-01 补：合并后暴露的 schema 冲突已按「代码让步、不动生产库」解决，见第 5b 节。
> 日常链路实测通过：`as_of=20260831`，环境中性，A 池 7 / B 池 30。

## 1. 收口前的问题

| 问题 | 严重度 |
|---|---|
| 龙虎榜 T01–T12 全部工作**从未提交**（159 文件处于未跟踪/未暂存状态，零备份） | 最高 |
| 主副本停在 `closers-g2-split`（08-23），实际开发与运行在 `v2r-final-integration` 工作树 | 高 |
| `origin/main` 落后本地全部工作 166 个提交 | 高 |
| 15 个本地分支 / 13 个 git 工作树 | 中 |
| 文档宣称的权威副本与实际运行副本不一致 | 中 |

## 2. 处置过程

1. **抢救**：`lhb-rescue-20260831` / `f3da54c`，159 文件 / +15180 行，推远端。
2. **合并**：`收口-20260831` 从 `v2r-final-integration` 拉出，并入抢救分支。
   - `closers-g2-split` 经 `merge-base --is-ancestor` 确认已被集成分支包含，可删。
   - 17 个冲突：7 个为前端构建产物（整体取集成分支后重新 `npm run build`），
     `Sidebar.tsx` 取集成分支，其余 9 个人工合并。
3. **构建**：`tsc -b && vite build` 通过，6 个龙虎榜 chunk 正常生成。
4. **测试**：4 个失败 → 全部修复 → `1144 passed`。
5. **快进**：`git push origin 收口-20260831:main`，`2c04962..b3f4d58`。

## 3. 合并中发现的两个真问题

**`components/common/` 被 git 静默删除。** 集成分支删了 `ApiErrorPanel` / `EmptyState` /
`StatusStrip`，龙虎榜分支未修改它们——「一边删、一边没动」不产生冲突，git 直接采纳删除。
6 个龙虎榜页面全部 import 这三个文件，`npm run build` 必然失败。已从抢救分支恢复。

**两个分支的 migration checksum 算法相反。** 集成分支的新算法是 `sha256:`（修「换个
worktree 检出 checksum 就变」），16 位是历史遗留，并建了 `_LEGACY_CHECKSUM_COMPATIBILITY`
做一次性映射；龙虎榜分支恰好相反。合并时若照搬龙虎榜的
`if checksum.startswith("sha256:") and len == 71: continue`，在新算法下会匹配**全部**
当前 checksum，等于把迁移漂移检测整体短路。已改为只跳过未注册的退役 id，历史 checksum
交回 `_checksum_matches` 经兼容表识别。

## 4. 四个测试失败的修复

| 失败 | 根因 | 处理 |
|---|---|---|
| `test_lhb_migrations` | 常量钉的是旧算法输出 | 刷新为新算法的 11 个值 |
| `test_migration_registry_v2` | 兼容用例用的是随手编的 `sha256:aaa…` | 改用兼容表里的真实历史值 |
| `test_v2_baseline_manifest` | `default_db_path()` 覆盖了 `--db-path` | 显式 `--db-path` 恢复优先 |
| `test_lhb_product_pipeline` | 共享 DAG 运行器新增 `ctx` 参数 | `build_lhb_dag` 加按签名过滤的适配层 |

## 5. 一处主动放宽的守卫（需知情）

`test_lean_product_surface::test_frontend_route_manifest_is_lean` 原本禁止 App.tsx 出现
**任何** `/v2/` 路由。现收窄为：`/lab`、`/paper` 照旧禁止，`/v2/` 仅允许 `/v2/lhb/`。

理由：龙虎榜由 8123 隔离产品（`scripts/serve_lhb_product.py`）服务，但与 8001
共用同一份 dist，路由删掉会导致 8123 界面白屏。**8001 的导航与 API 仍不含龙虎榜**，
由同文件另外两个用例把守，二者均通过且未改动。

## 5b. 龙虎榜迁移改为显式注册（2026-09-01）

合并后 8001 无法启动：

```
RuntimeError: 数据库 schema 与代码不兼容，拒绝启动
['MIGRATION_PENDING:v2:lhb_ops', 'MIGRATION_PENDING:v2:lhb_tracking']
```

两个分支的设计在此互斥：龙虎榜分支让迁移意图在模块导入时自注册，
而产品边界规定龙虎榜只活在隔离副本、生产库从不建 `lhb*` 表
（`migrate_v2.py` 甚至用 `assert_copy_database` 主动拒绝生产库）。
合并后二者相遇，启动断言就要求迁移 15.4 GB 的生产库。

**选择：让代码让步，不动生产库。**

- `lhb_tracking_v2.py` / `lhb_ops_v2.py`：删除模块底部的自注册调用。
  必须如此——`pit_writer` 仅为取 `LHB_PIT_HISTORY_TABLES` 就会 import 该模块，
  只改 `__init__.py` 拦不住。
- `migration_intents/__init__.py`：默认导入不含 LHB；新增 `register_lhb_intents()`。
- 显式调用方：`prepare_lhb_product_db.py`、`serve_lhb_product.py`、
  `run_lhb_eod.py`、`migrate_v2.py`（只接受副本）、`tests/conftest.py`（一次性临时库）。

结果：8001 启动正常（build `e20f1dbf54d1`），全量仍为 1144 passed，
生产库未被写入任何 DDL。

## 6. 日常使用

```
双击 每日运行.bat
```
同步行情 → 拉起 8001 → 发起扫描 → 打印 A/B 池与市场环境。
`-Root` 已指向主副本；`LIVE_TRADING_ENABLED` / `DAILY_SCHEDULER_ENABLED` /
`V2_PIT_READ_ENABLED` 在脚本内强制 false；拒绝指向 `lhb_product.db`。

只读体检：`双击 收口诊断.bat`。

## 7. 仍未通过的门禁

`readiness=BLOCKED`，七道闸门全部 fail-closed。**合并不改变这一点。**

| 闸门 | 原因 | 出路 |
|---|---|---|
| D | 门禁报告超 24h、构建版本与数据库指纹不符 | 在当前 build 重跑门禁 |
| S / P / L / G | 证据身份不匹配（证据生成于 2026-08-29 08:31） | 在当前 build 重新生成证据 |
| O | `AB_BACKUP_ROOT` 未配置；soak 0/5 | 变量已由 `daily_run.ps1` 设置；soak 需攒满 5 日 |
| R | 权威研究任务 `v2auth20260829k` 结论 **FAIL** | **无出路**——这是研究结论，非工程问题 |

龙虎榜第 6 节三道硬门同样未变：全仓 Ruff 存量债可清；官方跨源核验属数据授权问题；
shadow maturity 按定义需 3–12 个月。

## 8. 可选清理

- 12 个已完成的 `v2r-*` 工作树与分支：`git worktree remove` + `git branch -d`。
- `closers-g2-split`：已被包含，可删。
- `runtime\lhb_product.db` 约 15.4 GB：短期不推进龙虎榜可删除。

## 9. 明确不做的事

- 不打开 `LIVE_TRADING_ENABLED`、`DAILY_SCHEDULER_ENABLED`、`V2_PIT_READ_ENABLED`。
- 不把 A 池、B 池或龙虎榜信号描述为荐股、可跟单或已验证有效。
- 不覆盖 `docs/STATUS.md`，不改 `configs/platform_v2.yaml`，不动生产库。
- 不把 `MANUAL_RESEARCH` 参数描述为通过验证的参数。
- 不为了让门禁好看而改写验收结论——工程 PASS 不等于 edge PASS。
