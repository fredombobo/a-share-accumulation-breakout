# 完善计划 — 2026-08-20

> 对齐既有 v2 DAG（P0–P6 完成、P7.2/P8 未关）。**不宣布** `PERSONAL_INSTITUTIONAL_READY`。  
> 不改 `docs/STATUS.md` / `docs/RESEARCH-ROADMAP.md`。`LIVE_TRADING_ENABLED` 保持 false。

## 总目标

把「个人研究选股系统」收成可维护的机构级框架：API 无 SQLite/子进程、根脚本迁包、证据门禁诚实、文档收敛。

## 批次 / Gate / 验收

| Gate | 批次 | 准入 | 验收（全过才可进入下一 Gate） |
|------|------|------|------------------------------|
| **G0** | 计划冻结 | 工作区可改代码 | 本文档落地；不改业务行为 |
| **G1** | 架构债务清零 | G0 | `check_architecture.py --strict` = 0；API 层 0 条 `sqlite3`/`subprocess`；HTTP 契约不变；离线 pytest 绿 |
| **G2** | `backend_app.py` 拆路由 | G1 | 宿主文件只装配；legacy `/api/*` 迁 `ab_screener/api/routers/`；OpenAPI 无重复 path |
| **G3** | 根脚本迁包 | G2 | `signals`/`local_store`/`run_screener` 等根模块有 `ab_screener` 正式入口；根文件变薄 re-export |
| **G4** | 回测/扫描性能 | G3 | 回测按 code 切片复用；扫描/证据路径无 N+1 热循环；bench 不回退 |
| **G5** | 质量门加严 | G1 可并行 | CI = ruff + mypy 扩围 + pytest + arch `--strict` + frontend build |
| **G6** | 文档索引 | 任意 | `docs/INDEX.md` 指向当前路径；历史验收文档降为 archive 链接 |
| **G7** | Logic Platform / 研究闸门 | G1 | Phase 2/3 仅 research_only；进纸面必须 DSL+闸门 |
| **G8** | P8 诚实验收 | G2 + 用户合并 STATUS | 七闸门 PASS 或诚实 BLOCKED；Agent **不得**自行宣布就绪 |

## 本轮实施：G0 + G1

**做：** 把 `web/backend_app.py`、`ab_screener/api/scan_router.py`、`ab_screener/api/routers/paper.py` 的 SQLite/子进程下沉到 `ab_screener/data/*` 与 `ab_screener/application/scan_spawn.py`；架构检查覆盖 `api/**`；`--strict` 白名单清空。

**不做：** 大页面导航壳、真实 Token 回填、改冻结入场定义、实盘。

**回归命令**

```powershell
.\.venv312\Scripts\python.exe scripts\check_architecture.py --strict
.\.venv312\Scripts\python.exe -m ruff check web\backend_app.py ab_screener scripts\check_architecture.py tests
.\.venv312\Scripts\python.exe -m pytest tests\ -q -k "not browser"
```

失败 = G1 未完成，先修再报。
