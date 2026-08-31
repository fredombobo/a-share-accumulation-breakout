# 个人机构化收口 — 独立检查（2026-08-22）

- 检查 Agent: 独立检查（Grok，非实现 Agent）
- 契约: `PERSONAL-INSTITUTIONAL-CLOSERS-2026-08-21`
- 实现方 handoff: `docs/handoffs/CLOSERS-E2-2026-08-22.md`、`CLOSERS-E3-2026-08-22.md`
- 代码: 本地分支 `closers-g2-split` **`a86b03e`**（领先 `origin/main` `2c04962` 共 10 commit）
- Python: `.venv312\Scripts\python.exe` 3.12.10（本轮亲自跑通）
- 总评: **BLOCKED**（产品七闸门未关；E2 有必须修的回归）

实现方自签 `READY_FOR_REVIEW` **不作为通过证据**。禁止宣布 `PERSONAL_INSTITUTIONAL_READY`。

## 复跑命令（检查方亲自执行）

```text
check_architecture.py --strict     architecture OK   exit=0
pytest tests/ -q -k "not browser"  659 passed, 1 failed, 3 warnings in 342.88s   exit=1
ruff check web/backend_app.py ab_screener/api --select F821   4 errors  exit=1
flags load_resolved_config         LIVE=false；除 dual-run 外生产项 false
```

唯一 pytest 失败：`tests/test_v2_baseline_manifest.py::test_identity_stable_across_runs`  
`capture_v2_baseline.py --skip-api --skip-pytest` 在 12.3GB 库上 120s 超时。脚本不 import 被拆模块，**判定为既有环境问题，非 E2/E3 引入**。G2-4 因此不能写「全绿」，记 **PASS(with known timeout)**。

## 波次判定

| 波次 | 实现方 | 检查方 | 说明 |
|---|---|---|---|
| W0 环境 | — | PASS | venv 3.12 可用；库只读可开 |
| E2 / G2 拆路由 | READY_FOR_REVIEW | **REJECTED（须修）** | 结构完成；扫描完成路径与 Lab JSON 下载有 `NameError` |
| E3 / G3 根脚本迁包 | READY_FOR_REVIEW | **ACCEPTED_ENGINEERING_SLICE** | 6 模块双路径 import 成立；`_DB_DIR` 指向项目 `runtime` |
| D 数据 | 无 handoff | **未做** | daily 仍 20260818；fina/holder/stock_basic=0；无 coverage 文件 |
| O 运维 | 无 handoff | **未做** | `AB_BACKUP_ROOT` 空；soak 目录不存在；`dag_runs=0` |
| E4 性能 | 无 | 未做 | |
| E5 CI 加严 | yaml 已 `--strict` | **未关** | 本机 ruff 对拆分文件失败；CI 只监听 `main`/`codex/**`，当前分支名不会跑 CI |
| E6 文档 | INDEX 已入库 | 部分 | 收口五件套在分支内 |
| R 研究 | 无 | 未做 | `research_candidates=0` |
| F 旗标 | 未改 yaml | PASS（保持关） | 正确未开 |
| G 七闸门 | — | **BLOCKED** | D/O 仍 INSUFFICIENT；R 仍 FAIL |

## E2 明细

| ID | 结果 | 证据 |
|---|---|---|
| G2-1 宿主只装配 | PASS(有灰尘) | `web/backend_app.py` 约 252 行；业务路由已 `include_router`。仍残留未使用的 `pandas`/`BaseModel`/`signals`/`scoring` 与整段 legacy_state 导入（ruff F401） |
| G2-2 无重复 path | PASS | 全量套件含 `test_openapi_contract_v2.py`（3 用例）随 659 passed |
| G2-3 HTTP 契约 | PASS | `/api/*` 与 `/api/v2/*` 仍装配；未删纸面/扫描 |
| G2-4 离线测试 | PASS(with known timeout) | 659 passed / 1 failed（基线超时） |
| G2-5 架构 | PASS | `--strict` exit 0；`backend_app` 无 sqlite3/subprocess import |
| G2-6 扫描完成语义 | **FAIL** | 见缺陷 |

### 必须修复（阻断 E2 合入）

1. `ab_screener/api/routers/legacy_scan.py` 完成扫描时使用 `_BUILD_VERSION`、`_OVERVIEW_CACHE`，但未从 `legacy_state` 导入。真实扫描成功路径会 `NameError`，overview 缓存也不会失效。
2. `ab_screener/api/routers/legacy_lab.py:638` `json.dumps(...)` 未 `import json`。Lab 报告 `format=json` 下载会 `NameError`。

ruff `--select F821` 本轮 4 条，均落在上述两处。离线测试未覆盖这两条路径，所以 659 passed **不能**证明扫描完成可用。

## E3 明细

| ID | 结果 | 证据 |
|---|---|---|
| G3-1 正式入口 | PASS | `from signals import detect_accumulation_breakout` 与 `ab_screener.signals` 为同一对象 |
| G3-2 薄 re-export | PASS | 根 `signals.py` 等为 `import ab_screener.x as _m` + `globals().update` |
| G3-3 离线 pytest | PASS(with known timeout) | 同 G2-4；定向数据质量/宇宙测试在全量中绿 |
| 路径基准 | PASS | `ab_screener.local_store._DB_DIR` = `...\accumulation_breakout\runtime` |

未迁：`config` / `charting` / `tushare_init`（handoff 已声明，不阻断 G3 切片）。

## 硬边界抽查（未破坏）

- `LIVE_TRADING_ENABLED=false`；yaml 生产旗标未打开
- `docs/STATUS.md` / `docs/RESEARCH-ROADMAP.md` 未被覆盖
- 生产路径无新增 `INSERT OR REPLACE`（仅测试夹具 + 一条注释）
- ENTRY V1 golden 随全量套件绿
- astock 桥文件本波未改写边界

## 产品距离（相对最终态）

最终产品 = 设计合同的 `PERSONAL_INSTITUTIONAL_READY`（七闸门全 PASS + 身份一致），**不是** Wind/Bloomberg。

| 终点 | 现在 | 本轮是否缩短 |
|---|---|---|
| `PERSONAL_INSTITUTIONAL_READY` | 仍远；R 为 FAIL | **否** |
| `ENGINEERING_READY_RESEARCH_BLOCKED` | 仍未到（D+O 未过） | **否** |
| 工程债 G2/G3 | 结构已搬；E2 有回归未合 main | **部分缩短** |
| GitHub `main` 上的产品 | 仍停在 `2c04962` | **否**（10 commit 仅在本地分支） |

七闸门（相对 2026-08-21 审计，数据面无变化）：

| 闸门 | 状态 |
|---|---|
| D | INSUFFICIENT（日线 20260818；空表 3 个；PIT 读关） |
| R | FAIL（无新候选） |
| S/P | 实现在、生产未用 |
| L | 纸面小账本仍在 |
| O | INSUFFICIENT（无备份根、无 soak、DAG=0） |
| G | 实现在；身份未收口 |

## 下一步（按优先级）

1. **修 E2 F821**（scan 导入 `_BUILD_VERSION`/`_OVERVIEW_CACHE`；lab `import json`），加覆盖扫描完成与 lab JSON 的测试，再复跑 ruff F821=0。
2. 开 PR 合入 `main`（CI 分支过滤目前不含 `closers-g2-split`）。
3. Wave D：`sync_daily` + PIT 空表副本回填（仍不要开 PIT 读旗标）。
4. Wave O-min：用户提供 `AB_BACKUP_ROOT` 后备份+恢复演练。
5. 不要开 LIVE、不要把研究 FAIL 推进 A 池。

## 总评用词

```text
overall: BLOCKED
E2: REJECTED (must-fix NameError on scan complete / lab json)
E3: ACCEPTED_ENGINEERING_SLICE
suggested_status: BLOCKED
claimed_ready: no
```
