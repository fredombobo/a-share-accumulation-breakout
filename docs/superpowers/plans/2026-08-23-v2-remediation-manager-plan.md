# v2.0 Remediation and Multi-Agent Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 把当前“v2 代码骨架大量存在但生产闭环未启用”的状态，收口为可独立复验、可诚实判定的个人机构化研究与纸面交易平台。

**Architecture:** 采用独立 worktree、按领域独占文件、三波合并。第一波修基础正确性并准备各领域；第二波接生产路径、研究证据和信息覆盖层；第三波统一共享入口、质量门和七闸门证据。实现 Agent 不更新最终状态，管理者独立复验后才接受。

**Tech Stack:** Python 3.12、FastAPI、SQLite、React 19、TypeScript、Vite、Pytest、Ruff、Mypy、Vitest、Playwright。

---

## 1. 管理身份与不可突破边界

- 管理者：负责分派、文件所有权、接口冻结、代码审查、复验、退回、合并顺序和 P8 最终裁决。
- 实现 Agent：只实现一个任务包，不修改最终状态，不宣布系统就绪。
- 权威 Python：

~~~powershell
$py = "E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe"
& $py --version
~~~

预期：Python 3.12.10。

- 固定基线：b6772c3001e1fa37447fca813b7ad3512b54eb49。
- LIVE_TRADING_ENABLED 永远为 false。
- 任何生产数据写入、迁移、PIT 正式读、执行写、风控 enforce、DAG 自动运行，都必须在管理者验收前保持关闭。
- 研究结果允许 FAIL 或 INSUFFICIENT_EVIDENCE。完整、可复现的失败证据也算研究任务完成；调阈值制造 PASS 不算。

## 2. 独立 worktree 创建

用户或调度 Agent 为每个实现 Agent 执行一次。不要让多个 Agent 共用当前脏工作区。

~~~powershell
New-Item -ItemType Directory -Force -Path E:\CODEX\Stock_selection\worktrees
git -C E:\CODEX\Stock_selection\accumulation_breakout worktree add E:\CODEX\Stock_selection\worktrees\v2r-q1 -b agent/v2r-q1 b6772c3
git -C E:\CODEX\Stock_selection\accumulation_breakout worktree add E:\CODEX\Stock_selection\worktrees\v2r-a -b agent/v2r-a b6772c3
git -C E:\CODEX\Stock_selection\accumulation_breakout worktree add E:\CODEX\Stock_selection\worktrees\v2r-d -b agent/v2r-d b6772c3
git -C E:\CODEX\Stock_selection\accumulation_breakout worktree add E:\CODEX\Stock_selection\worktrees\v2r-x -b agent/v2r-x b6772c3
git -C E:\CODEX\Stock_selection\accumulation_breakout worktree add E:\CODEX\Stock_selection\worktrees\v2r-f -b agent/v2r-f b6772c3
git -C E:\CODEX\Stock_selection\accumulation_breakout worktree add E:\CODEX\Stock_selection\worktrees\v2r-o1 -b agent/v2r-o1 b6772c3
~~~

第二波 worktree 只由管理者在 Wave 1 接受并生成 integration commit 后创建。实现 Agent 不自行猜测 SHA，也不得继续从旧 b6772c3 开始；管理者届时发送包含精确 SHA 和绝对 worktree 路径的启动命令。

## 3. 并行与文件所有权

| 任务 | 可并行 | 独占域 | 不得触碰 |
|---|---|---|---|
| V2R-Q1 | Wave 1 | today API、baseline capture | 数据、纸面执行、前端、共享入口 |
| V2R-A | Wave 1 | scanner orchestration/kernel | API、数据 adapter、策略阈值 |
| V2R-D | Wave 1 | PIT、公司行为、数据质量 | 生产 DB、tushare_init、flags |
| V2R-X | Wave 1 | 执行、纸面账本、风险 | 前端、app_factory、flags |
| V2R-F | Wave 1 | frontend src/tests/package | 后端、dist |
| V2R-O1 | Wave 1 | health、backup、restore | 生产调度 flag、删除备份 |
| V2R-S | Wave 2 | strategy/signal lifecycle | app_factory/config、研究阈值 |
| V2R-O2 | Wave 2 | DAG/scheduler/audit | 生产库先试、伪造 soak |
| V2R-R | Wave 2 | research evidence | A 池、订单、门禁阈值 |
| V2R-N | Wave 2 | read-only information overlay | A/B 资格、仓位、订单 |
| V2R-Q2 | Wave 3 | 集成质量债务 | 业务语义 |
| V2R-G | Wave 3 | shared entry/config/readiness/dist | 研究结果、LIVE flag |
| V2R-P8 | Manager | 七闸门和官方状态 | 实现 Agent 不得领取 |

---

### Task 1: V2R-Q1 — 正确性回归与快速可重复基线

**Files:**
- Modify: ab_screener/api/routers/legacy_misc.py
- Modify: tests/test_today_guide.py
- Modify: scripts/capture_v2_baseline.py
- Modify: tests/test_v2_baseline_manifest.py
- Create: tests/test_build_version.py
- Create: docs/handoffs/V2R-Q1.md

- [ ] **Step 1: 固定现有两个失败**

~~~powershell
& $py -m pytest tests/test_today_guide.py::test_today_api_returns_the_server_derived_action -q
& $py -m pytest tests/test_v2_baseline_manifest.py::test_identity_stable_across_runs -q
~~~

预期修改前：第一个 RUN_SCAN 与 DAILY_COMPLETE 不一致；第二个在大库上超过 120 秒。

- [ ] **Step 2: 把 today API 改成显式数据库依赖**

路由应采用现有 get_db_path 依赖，不再直接捕获 legacy_state._DB：

~~~python
@router.get("/api/today")
def today_guide(
    at: str | None = None,
    db_path: str = Depends(get_db_path),
) -> dict[str, object]:
    now = datetime.fromisoformat(at) if at else None
    return build_today_guide(db_path, now=now)
~~~

测试使用 FastAPI dependency_overrides，并在 finally 清理：

~~~python
backend.app.dependency_overrides[get_db_path] = lambda: str(db)
try:
    response = TestClient(backend.app).get(
        "/api/today",
        params={"at": "2026-08-07T18:00:00+08:00"},
    )
finally:
    backend.app.dependency_overrides.clear()
~~~

- [ ] **Step 3: 让 identity 测试使用小型临时数据库**

为 capture_v2_baseline.py 增加 --db-path，默认仍是 runtime/stock_data.db。测试创建含 daily、daily_basic、moneyflow、stock_basic、delisted_basic、scan_result、schema_version 的临时 SQLite，再把该路径传给两次子进程。不得把生产深校验改成永远跳过。

~~~python
parser.add_argument(
    "--db-path",
    default="runtime/stock_data.db",
    help="用于基线身份的 SQLite；默认生产库，测试必须传临时库",
)
db = db_facts((ROOT / args.db_path).resolve())
~~~

- [ ] **Step 4: 复验**

~~~powershell
& $py -m pytest tests/test_today_guide.py tests/test_v2_baseline_manifest.py tests/test_build_version.py -q
& $py -m ruff check ab_screener/api/routers/legacy_misc.py scripts/capture_v2_baseline.py tests/test_today_guide.py tests/test_v2_baseline_manifest.py
& $py -m mypy ab_screener/api/routers/legacy_misc.py scripts/capture_v2_baseline.py
~~~

验收：定向测试全绿；临时库 identity 两次一致且总耗时小于 30 秒；生产默认路径未改变。

- [ ] **Step 5: 提交**

~~~powershell
git add ab_screener/api/routers/legacy_misc.py tests/test_today_guide.py scripts/capture_v2_baseline.py tests/test_v2_baseline_manifest.py tests/test_build_version.py docs/handoffs/V2R-Q1.md
git commit -m "fix(v2r-q1): restore database injection and fast baseline identity test"
~~~

---

### Task 1A: V2R-A — 扫描内核拆分与确定性回归

**Files:**
- Modify: ab_screener/run_screener.py
- Create: ab_screener/screener/__init__.py
- Create: ab_screener/screener/data_loader.py
- Create: ab_screener/screener/prefilter.py
- Create: ab_screener/screener/evaluator.py
- Create: ab_screener/screener/orchestrator.py
- Preserve: run_screener.py
- Create: tests/test_screener_architecture.py
- Create: tests/test_screener_golden_result.py
- Create: docs/handoffs/V2R-A.md

- [ ] **Step 1: 固定重构前 golden**

使用小型固定市场 fixture，记录候选代码、A/B 池、各评分、拒绝原因和顺序。相同种子运行两次必须逐字段一致。

~~~python
def test_scanner_golden_result_is_stable(frozen_market_store):
    first = run_scan(store=frozen_market_store, as_of="20260807", workers=1)
    second = run_scan(store=frozen_market_store, as_of="20260807", workers=1)
    assert normalize(first) == normalize(second)
    assert [row["ts_code"] for row in normalize(first)] == EXPECTED_CODES
~~~

- [ ] **Step 2: 只做职责拆分**

data_loader 只读取/标准化输入；prefilter 只生成候选集合与理由；evaluator 只计算单标的结果；orchestrator 负责进程、取消、进度、排序和聚合。ENTRY、评分公式、阈值、默认参数和结果格式保持不变。

- [ ] **Step 3: 保留兼容入口**

根 run_screener.py 继续作为薄 re-export；ab_screener/run_screener.py 可保留公共 facade，但业务实现迁入 ab_screener/screener。不得破坏旧 import 和子进程 spawn。

- [ ] **Step 4: Windows 取消与进度回归**

覆盖父进程退出、取消事件、progress 临时文件并发写、单 worker/多 worker 相同结果。不得用 os.kill(ppid, 0)。

- [ ] **Step 5: 质量门**

~~~powershell
& $py -m pytest tests/test_screener_architecture.py tests/test_screener_golden_result.py tests/test_scan_spawn.py tests/test_scan_runtime.py tests/test_scan_progress_io.py tests/test_scan_guard.py -q
& $py scripts/check_architecture.py --strict
& $py -m ruff check ab_screener/run_screener.py ab_screener/screener run_screener.py
& $py -m mypy ab_screener/run_screener.py ab_screener/screener
~~~

验收：golden 逐字段不变；兼容 import 通过；ab_screener/run_screener.py 只保留 facade/orchestration，目标小于 350 行，拆分模块各自职责单一。

- [ ] **Step 6: 提交**

~~~powershell
git add ab_screener/run_screener.py ab_screener/screener run_screener.py tests/test_screener_architecture.py tests/test_screener_golden_result.py docs/handoffs/V2R-A.md
git commit -m "refactor(v2r-a): split deterministic scanner kernel"
~~~

---

### Task 2: V2R-D — PIT、公司行为与数据门禁

**Files:**
- Modify: ab_screener/data/adapters/tushare_pit.py
- Modify: ab_screener/data/corporate_action_repository.py
- Modify: ab_screener/application/data_quality.py
- Modify: ab_screener/application/pit_backfill.py
- Modify: paper_trading/real_data_gate.py
- Modify: scripts/backfill_pit_v2.py
- Test: tests/test_adjustment_asof.py
- Test: tests/test_data_quality_v2.py
- Create: tests/test_corporate_action_sync_v2.py
- Create: docs/handoffs/V2R-D.md

- [ ] **Step 1: 增加公司行为 PIT 失败测试**

覆盖 revision 切换、available_at 晚于 decision_at 不可见、无权限显式失败、重复抓取幂等。

~~~python
def test_corporate_action_is_not_visible_before_available_at(tmp_path):
    repo = CorporateActionRepository(tmp_path / "pit.db")
    repo.append(action_fixture(available_at="2026-08-22T18:00:00+08:00"))
    rows = repo.list_asof(
        "000001.SZ",
        decision_at="2026-08-22T17:59:59+08:00",
    )
    assert rows == []
~~~

- [ ] **Step 2: 实现适配器和可恢复 backfill**

每条记录必须具有 effective_at、available_at、ingested_at、source、revision。checkpoint 记录最后完成分区，不允许部分分区被标记完成。所有外部调用统一 from tushare_init import get_pro。

- [ ] **Step 3: 在数据库副本执行**

~~~powershell
$src = "E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db"
$copyRoot = "E:\ab-maintenance\v2r-d"
New-Item -ItemType Directory -Force -Path $copyRoot
Copy-Item -LiteralPath $src -Destination "$copyRoot\stock_data_copy.db"
& $py scripts/backfill_pit_v2.py --db "$copyRoot\stock_data_copy.db" --preflight
& $py scripts/backfill_pit_v2.py --db "$copyRoot\stock_data_copy.db" --resume
~~~

不得对生产库运行 --apply。

- [ ] **Step 4: 生成 shadow parity 报告**

固定种子抽取至少 20 标的、5 个日期，比较 legacy 与 PIT as-of 读取。价格/量字段需完全一致或按源精度一致；报告必须写 code SHA、config hash、DB fingerprint、样本和差异。

- [ ] **Step 5: 运行副本数据门禁**

~~~powershell
& $py -m paper_trading.real_data_gate --days 730 --db "$copyRoot\stock_data_copy.db" --report "$copyRoot\gates"
~~~

无 Token、公司行为接口无权限或持仓覆盖不足都必须非零退出；不能标 PASS。

- [ ] **Step 6: 定向质量门**

~~~powershell
& $py -m pytest tests/test_adjustment_asof.py tests/test_data_quality_v2.py tests/test_corporate_action_sync_v2.py -q
& $py -m ruff check ab_screener/data ab_screener/application/data_quality.py ab_screener/application/pit_backfill.py paper_trading/real_data_gate.py scripts/backfill_pit_v2.py
& $py -m mypy ab_screener/data ab_screener/application/data_quality.py ab_screener/application/pit_backfill.py paper_trading/real_data_gate.py
~~~

- [ ] **Step 7: 提交**

~~~powershell
git add ab_screener/data ab_screener/application/data_quality.py ab_screener/application/pit_backfill.py paper_trading/real_data_gate.py scripts/backfill_pit_v2.py tests docs/handoffs/V2R-D.md
git commit -m "feat(v2r-d): close PIT corporate-action and data-gate evidence gaps"
~~~

---

### Task 3: V2R-X — 执行、账本 parity 与风险接线

**Files:**
- Modify: ab_screener/domain/execution/dual_run.py
- Modify: paper_trading/engine.py
- Modify: paper_trading/orders.py
- Modify: paper_trading/risk_adapter.py
- Modify: paper_trading/guidance.py
- Modify: ab_screener/api/routers/legacy_paper.py
- Create: tests/test_execution_dual_run_integration.py
- Create: tests/test_risk_review_confirm_parity.py
- Create: docs/handoffs/V2R-X.md

- [ ] **Step 1: 写 dual-run 失败测试**

同一冻结行情、规则和订单分别交给 legacy 与 v2 核心；断言成交数量、成交价、佣金、税费、其他费用、现金变化和持仓变化逐项一致。

~~~python
def test_dual_run_has_zero_fen_difference(frozen_buy_order):
    result = compare_round_trip(frozen_buy_order)
    assert result.quantity_diff == 0
    assert result.cash_diff_fen == 0
    assert result.fee_diff_fen == 0
~~~

- [ ] **Step 2: 让 review 与 confirm 调用同一个风险入口**

evaluate_order_risk 必须由预览与确认共同调用；确认不能相信前端提交的风控结果。observe 模式记录结果但不改变现有拒单，enforce 模式测试可拒绝，配置默认保持 false。

~~~python
risk = evaluate_order_risk(
    db_path=db_path,
    account_id=order.account_id,
    side=order.side,
    ts_code=order.ts_code,
    quantity=order.quantity,
    decision_at=order.confirmed_at,
)
~~~

- [ ] **Step 3: 保持默认写路径关闭**

V2_EXECUTION_WRITE_ENABLED=false 时既有成交结果不变；V2_EXECUTION_DUAL_RUN_ENABLED=true 时只记录比较证据，不得写第二笔成交、现金或持仓。

- [ ] **Step 4: 覆盖失败语义**

必须测试：停牌、无报价、一字涨停买、一字跌停卖、部分成交、T+1、负现金、超卖、风险 observe/enforce、重复确认和重复撮合。

- [ ] **Step 5: 定向质量门**

~~~powershell
& $py -m pytest tests/test_execution_core_v2.py tests/test_execution_lineage.py tests/test_execution_dual_run_integration.py tests/test_risk_review_confirm_parity.py tests/test_order_risk_integration.py tests/test_paper_guidance.py tests/test_paper_api_acceptance.py -q
& $py -m ruff check ab_screener/domain/execution ab_screener/domain/risk paper_trading/engine.py paper_trading/orders.py paper_trading/risk_adapter.py paper_trading/guidance.py ab_screener/api/routers/legacy_paper.py
& $py -m mypy ab_screener/domain/execution ab_screener/domain/risk paper_trading/engine.py paper_trading/orders.py paper_trading/risk_adapter.py paper_trading/guidance.py ab_screener/api/routers/legacy_paper.py
~~~

- [ ] **Step 6: 提交**

~~~powershell
git add ab_screener/domain/execution ab_screener/domain/risk paper_trading tests/test_execution_dual_run_integration.py tests/test_risk_review_confirm_parity.py docs/handoffs/V2R-X.md
git commit -m "feat(v2r-x): wire execution parity and shared pretrade risk"
~~~

---

### Task 4: V2R-F — 前端缺页与自动化验收

**Files:**
- Modify: web/frontend/package.json
- Modify: web/frontend/src/App.tsx
- Modify: web/frontend/src/layout/Sidebar.tsx
- Modify: web/frontend/src/hooks/useFeatureFlag.ts
- Create: web/frontend/src/pages/v2/Monitor.tsx
- Create: web/frontend/src/pages/v2/Review.tsx
- Create: web/frontend/src/pages/v2/System.tsx
- Create: web/frontend/src/pages/v2/Compare.tsx
- Create: web/frontend/src/api/platform.ts
- Create: web/frontend/tests/v2-guided-flow.spec.ts
- Create: web/frontend/tests/v2-pages.test.tsx
- Create: web/frontend/playwright.config.ts
- Create: docs/handoffs/V2R-F.md

- [ ] **Step 1: 建立测试脚本**

package.json 至少增加：

~~~json
{
  "scripts": {
    "test": "vitest run",
    "test:e2e": "playwright test",
    "test:a11y": "playwright test --grep @a11y"
  }
}
~~~

- [ ] **Step 2: 页面先写失败组件测试**

断言四个目标页面都有加载、空态、错误、证据不足和正常状态；不显示原始 JSON；键盘可触发主操作；390px 不横向溢出。

- [ ] **Step 3: 实现页面并复用 typed clients**

页面只显示后端金额字符串和风险结论，不自行重算。System 页必须把“快速健康”和“最后一次深度完整性检查”分开显示。Compare 只允许 2–6 标的。

- [ ] **Step 4: 移除本地业务 flag 覆盖**

useFeatureFlag 不得允许 query string/localStorage 打开服务端关闭的执行、风险、调度或控制台旗标。本地仅可保存引导/专业视图偏好。

- [ ] **Step 5: 修正可信验证推荐预设**

引导模式固定 strategy=A/B、mode=grid、max_codes=600、step=5、自动窗口；专业视图仍可调整。PASS 文案必须说明不自动进入 A 池。

- [ ] **Step 6: 自动化**

~~~powershell
npm --prefix web/frontend run test
npm --prefix web/frontend run build
npm --prefix web/frontend run test:e2e
~~~

Playwright 覆盖页面切换、窗口失焦/恢复、刷新恢复、390px 和键盘。测试使用隔离账户或 mock，不写真实纸面账户。

- [ ] **Step 7: 提交且排除 dist**

~~~powershell
git add web/frontend/package.json web/frontend/package-lock.json web/frontend/vite.config.ts web/frontend/playwright.config.ts web/frontend/src web/frontend/tests docs/handoffs/V2R-F.md
git diff --cached --name-only | Select-String "web/frontend/dist" -Quiet
git commit -m "feat(v2r-f): complete v2 console pages and browser gates"
~~~

若 Select-String 命中 dist，停止提交并从暂存区移除 dist；不得删除磁盘文件。

---

### Task 5: V2R-O1 — 快速健康、备份与恢复

**Files:**
- Modify: ab_screener/operations/health.py
- Modify: ab_screener/operations/backup.py
- Modify: ab_screener/api/routers/system.py
- Modify: scripts/restore_backup.ps1
- Modify: scripts/soak_monitor_v2.py
- Create: scripts/check_db_integrity.py
- Create: tests/test_system_health_fast.py
- Create: tests/test_restore_backup_contract.py
- Create: docs/handoffs/V2R-O1.md

- [ ] **Step 1: 写健康接口性能失败测试**

monkeypatch sqlite execute；若 GET 路径请求 PRAGMA integrity_check 或 quick_check，测试直接失败。快速健康只允许打开只读连接、读取 schema/version/latest date/WAL 和既有完整性证书。

~~~python
def test_fast_health_never_runs_full_integrity_check(monkeypatch, tiny_db):
    payload = system_health(tiny_db, backup_root=tiny_db.parent)
    assert payload["database"]["deep_check"]["status"] in {"PASS", "STALE", "MISSING"}
~~~

- [ ] **Step 2: 拆分快速检查与离线深检**

GET /api/v2/system/health 目标热请求低于 500ms。scripts/check_db_integrity.py 独立运行 PRAGMA integrity_check，输出包含 DB fingerprint、开始/完成时间、结果和 SHA-256 的 JSON；接口仅读取匹配当前 DB fingerprint 的最新报告。

- [ ] **Step 3: 接线 AB_BACKUP_ROOT**

服务未设置时明确返回 BACKUP_ROOT_UNCONFIGURED，不能悄悄把 runtime/backups 当通过。不得把 HTTP 查询参数作为生产 backup_root 覆盖来源。

- [ ] **Step 4: 严格恢复演练**

restore_backup.ps1 -DryRun 必须在无交互终端 exit 0，且打印解析后的源备份、临时恢复目标、只读检查和不会覆盖生产库。真实演练恢复到新临时目录并比较关键表行数与 schema。

- [ ] **Step 5: 定向质量门**

~~~powershell
& $py -m pytest tests/test_backup_restore.py tests/test_system_health_fast.py tests/test_restore_backup_contract.py -q
& $py -m ruff check ab_screener/operations ab_screener/api/routers/system.py scripts/check_db_integrity.py
& $py -m mypy ab_screener/operations ab_screener/api/routers/system.py scripts/check_db_integrity.py
powershell -ExecutionPolicy Bypass -File scripts/restore_backup.ps1 -BackupRoot E:\ab-backups -DryRun
~~~

- [ ] **Step 6: 提交**

~~~powershell
git add ab_screener/operations/health.py ab_screener/operations/backup.py ab_screener/api/routers/system.py scripts/restore_backup.ps1 scripts/check_db_integrity.py tests/test_system_health_fast.py tests/test_restore_backup_contract.py docs/handoffs/V2R-O1.md
git commit -m "fix(v2r-o1): make health fast and backup recovery verifiable"
~~~

---

### Task 6: V2R-S — 六形态与信号生产接线

**Files:**
- Modify: ab_screener/application/signal_pipeline.py
- Modify: ab_screener/application/signal_outcomes.py
- Modify: ab_screener/api/routers/legacy_scan.py
- Modify: ab_screener/domain/signal_lifecycle.py
- Create: tests/test_signal_pipeline_production_wiring.py
- Create: tests/test_signal_fill_lifecycle_integration.py
- Create: docs/handoffs/V2R-S.md

- [ ] **Step 1: 先测扫描完成后的幂等写入**

同一 scan_run_id 重放两次，observation 只有一条；新 revision 创建新 observation，不覆盖历史记录。

- [ ] **Step 2: 插件状态硬门**

EXPERIMENTAL 只产生观察记录和 B/研究展示，不得生成 A 池可交易信号或买入草稿。只有已通过研究晋级且配置允许的版本才可 ACTIVE_FOR_A_POOL。

- [ ] **Step 3: 成交驱动 ENTERED**

订单确认不改变 ENTERED；只有实际 fill 事件触发。零成交、过期和拒绝不得伪装为 ENTERED。

- [ ] **Step 4: outcome 时点**

ret_5/10/20 只在相应交易日完成且行情 available_at 合法后回填；UNFILLABLE 保持 NULL，不写 0。

- [ ] **Step 5: 定向质量门**

~~~powershell
& $py -m pytest tests/test_signal_observations.py tests/test_signal_lifecycle_v2.py tests/test_signal_outcomes.py tests/test_signals_v2.py tests/test_signal_pipeline_production_wiring.py tests/test_signal_fill_lifecycle_integration.py -q
& $py -m ruff check ab_screener/strategies ab_screener/application/signal_pipeline.py ab_screener/application/signal_outcomes.py ab_screener/api/routers/legacy_scan.py
& $py -m mypy ab_screener/strategies ab_screener/application/signal_pipeline.py ab_screener/application/signal_outcomes.py
~~~

- [ ] **Step 6: 提交**

~~~powershell
git add ab_screener/strategies ab_screener/application/signal_pipeline.py ab_screener/application/signal_outcomes.py ab_screener/api/routers/legacy_scan.py ab_screener/domain ab_screener/data tests/test_signal_pipeline_production_wiring.py tests/test_signal_fill_lifecycle_integration.py docs/handoffs/V2R-S.md
git commit -m "feat(v2r-s): connect immutable signals to scan and fill lifecycle"
~~~

---

### Task 7: V2R-O2 — EOD DAG、审计与故障恢复

**Files:**
- Modify: ab_screener/operations/dag.py
- Modify: ab_screener/operations/scheduler.py
- Modify: ab_screener/application/audit_service.py
- Modify: ab_screener/data/scheduler_repository.py
- Create: tests/test_daily_dag_closed_loop.py
- Create: tests/test_fault_injection_scheduler.py
- Create: docs/handoffs/V2R-O2.md

- [ ] **Step 1: 固定 DAG 顺序**

顺序必须是数据新鲜/公司行为检查 → 释放可卖 → 撮合 → 估值 → 风险/损益 → 对账 → 信号 outcome → 下一交易日草稿 → manifest。上游失败阻断下游，重试保留 attempt。

- [ ] **Step 2: 幂等与租约测试**

相同账户/交易日只能一个成功 run；崩溃后租约过期可续跑；重复运行不增加成交、现金、持仓或信号。

- [ ] **Step 3: 故障注入**

至少覆盖同步失败、缺行情、公司行为未处理、撮合中断、对账一分钱差异、审计写失败和进程重启。测试标记必须注册为 fault_injection，使 pytest -m fault_injection 实际收集用例。

- [ ] **Step 4: 副本演练**

复制生产 DB 到 E:\ab-maintenance\v2r-o2，保持 DAILY_SCHEDULER_ENABLED=false，手工调用 runner 完成固定历史交易日。验证 dag_runs、step_runs、leases、audit_events 非零且重放稳定。

- [ ] **Step 5: 启动真实时间证据**

soak 监控从接受后的第一个交易日开始记录；未满五个交易日时 O 闸门保持 BLOCKED。不得用历史时间戳伪造观察天数。

- [ ] **Step 6: 质量门**

~~~powershell
& $py -m pytest tests/test_daily_dag_closed_loop.py tests/test_fault_injection_scheduler.py -q
& $py -m pytest -m fault_injection -q
& $py -m ruff check ab_screener/operations ab_screener/application/audit_service.py ab_screener/data/scheduler_repository.py
& $py -m mypy ab_screener/operations ab_screener/application/audit_service.py ab_screener/data/scheduler_repository.py
~~~

- [ ] **Step 7: 提交**

~~~powershell
git add ab_screener/operations ab_screener/application/audit_service.py ab_screener/data/scheduler_repository.py tests/test_daily_dag_closed_loop.py tests/test_fault_injection_scheduler.py docs/handoffs/V2R-O2.md
git commit -m "feat(v2r-o2): close persistent daily DAG and recovery loop"
~~~

---

### Task 8: V2R-R — 完整可信研究证据

**Files:**
- Modify only if a statistical implementation defect is proven: ab_screener/research/**
- Test only if code changes: tests/test_*research*.py and tests/test_*walkforward*.py
- Create: docs/handoffs/V2R-R.md
- Generate: runtime/v2/research_A_*/trusted_report_*.json

- [ ] **Step 1: 前置状态**

~~~powershell
Invoke-RestMethod http://127.0.0.1:8001/api/lab/research-status?probe_token=false | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8001/api/lab/status | ConvertTo-Json -Depth 8
~~~

要求 research_mode=full、至少 730 交易日、没有仍在运行的任务。

- [ ] **Step 2: 提交冻结请求**

~~~powershell
$body = @{
  strategy = "A"
  is_start = ""
  is_end = ""
  oos_start = ""
  oos_end = ""
  max_codes = 600
  step = 5
  mode = "grid"
  force = $true
} | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/api/lab/optimize -ContentType "application/json" -Body $body
$run | ConvertTo-Json -Depth 8
~~~

- [ ] **Step 3: 查询而非重启任务**

用返回 task_id 查询 /api/lab/status?task_id=，离开页面不重复 POST。最终报告必须包含：完整成本、随机与 MA 基线、至少三个 WF 窗口、PBO、DSR、MinTRL、容量和代码/数据/config 身份。

- [ ] **Step 4: 诚实裁决**

OOS 净回撤、WF 或反过拟合失败时 verdict 必须 FAIL 或 INSUFFICIENT_EVIDENCE，candidate_eligible=false。不得改 25% 回撤线、筛掉失败窗或换参数后只保留最佳报告。

- [ ] **Step 5: 若发现代码缺陷**

先写最小失败 fixture，单独 commit 修统计实现，再从头重跑完整研究。任何代码改动必须由独立复核者检查公式。

- [ ] **Step 6: 提交 handoff**

~~~powershell
git add docs/handoffs/V2R-R.md
git commit -m "docs(v2r-r): record full 600-by-step5 research evidence"
~~~

运行产物如体积大或含数据库身份，仅登记路径和 SHA-256；是否提交由管理者决定。

---

### Task 9: V2R-N — 信息获取增强覆盖层

**Files:**
- Create: ab_screener/intelligence/national_team_overlay_v1.py
- Create: ab_screener/data/adapters/ntm_client.py
- Create: ab_screener/application/evaluate_overlays.py
- Create: configs/intelligence/national_team_overlay_v1.yaml
- Create: tests/test_national_team_overlay.py
- Create: tests/test_ntm_client.py
- Create: tests/test_evaluate_overlays.py
- Create: docs/handoffs/V2R-N.md

实施必须同时遵循：

- docs/superpowers/plans/2026-08-22-ntm-p1-overlay.md
- docs/superpowers/plans/2026-08-22-ntm-p2-wiring.md

- [ ] **Step 1: 先冻结只读契约**

输出包括 observation_at、effective_at、available_at、ingested_at、source、revision、confidence、evidence_refs。没有 source 或 available_at 的记录不得用于历史决策时点。

- [ ] **Step 2: 无外部服务 fixture**

单元测试完全离线，固定原始响应和时点。接口权限不足必须返回 INSUFFICIENT，不得伪造数据。

- [ ] **Step 3: 证明不影响交易**

同一行情/扫描输入，启用或禁用 overlay 后 A/B 池资格、目标仓位和纸面订单必须逐项一致。覆盖层只能解释、标记或排序研究观察，不得成为资格条件。

- [ ] **Step 4: 质量门**

~~~powershell
& $py -m pytest tests/test_national_team_overlay.py tests/test_ntm_client.py tests/test_evaluate_overlays.py -q
& $py -m ruff check ab_screener/intelligence/national_team_overlay_v1.py ab_screener/data/adapters/ntm_client.py ab_screener/application/evaluate_overlays.py
& $py -m mypy ab_screener/intelligence/national_team_overlay_v1.py ab_screener/data/adapters/ntm_client.py ab_screener/application/evaluate_overlays.py
~~~

- [ ] **Step 5: 提交**

~~~powershell
git add ab_screener/intelligence/national_team_overlay_v1.py ab_screener/data/adapters/ntm_client.py ab_screener/application/evaluate_overlays.py configs/intelligence/national_team_overlay_v1.yaml tests/test_national_team_overlay.py tests/test_ntm_client.py tests/test_evaluate_overlays.py docs/handoffs/V2R-N.md
git commit -m "feat(v2r-n): add PIT-safe read-only institutional overlay"
~~~

---

### Task 10: V2R-Q2 — 集成质量债务清零

**Files:** 由管理者根据已经接受的集成 diff 逐个授权。默认只做 import、typing、测试标记和质量配置。

- [ ] **Step 1: 记录当前失败集合**

~~~powershell
& $py -m ruff check . --exclude web/frontend/node_modules
& $py -m mypy ab_screener paper_trading logic_platform web/backend_app.py
& $py -m pytest -q -k "not browser"
~~~

- [ ] **Step 2: 机械修复与语义修复分开提交**

第一提交只处理 F401/I001/typing；第二提交处理实际类型或测试问题。不得扩大 Ruff/Mypy ignore，不得删除失败测试。

- [ ] **Step 3: 完整门禁**

~~~powershell
& $py scripts/check_architecture.py --strict
& $py -m ruff check . --exclude web/frontend/node_modules
& $py -m mypy ab_screener paper_trading logic_platform web/backend_app.py
& $py -m pytest -q -k "not browser"
& $py -m pytest -m performance -q
& $py -m pytest -m fault_injection -q
npm --prefix web/frontend run test
npm --prefix web/frontend run build
~~~

所有命令 exit 0；performance 和 fault_injection 必须实际收集测试，不能以 0 tests 通过。

---

### Task 11: V2R-G — 共享入口、flags 与 readiness 收口

**Files:**
- Modify: web/backend_app.py
- Modify: ab_screener/api/app_factory.py
- Modify: ab_screener/application/platform_config.py
- Modify: ab_screener/domain/readiness.py
- Create: ab_screener/api/routers/readiness.py
- Create: tests/test_readiness_v2.py
- Modify: configs/platform_v2.yaml
- Modify: AGENTS.md
- Modify after V2R-F acceptance: web/frontend/src/api/platform.ts
- Modify: web/frontend/src/layout/Sidebar.tsx
- Build: web/frontend/dist/**
- Create: docs/handoffs/V2R-G.md

- [ ] **Step 1: 修 readiness 纯逻辑**

worktree dirty 或 identity mismatch 必须先返回 BLOCKED；只有七门中仅 R 未过且身份干净时才返回 ENGINEERING_READY_RESEARCH_BLOCKED；全部 PASS 返回 PERSONAL_INSTITUTIONAL_READY。

~~~python
if not ri.worktree_clean or not ri.identity_matches:
    status = STATUS_BLOCKED
elif research_fail_only:
    status = STATUS_ENGINEERING_READY_RESEARCH_BLOCKED
elif blocked:
    status = STATUS_BLOCKED
else:
    status = STATUS_PERSONAL_INSTITUTIONAL_READY
~~~

- [ ] **Step 2: 增加生产调用点**

GET /api/v2/readiness 必须读取当前 D/R/S/P/L/O/G 证据和身份，而不是接受客户端传入布尔值。GET /api/v2/platform/status 永久可读，返回 resolved flags、config hash、build、LIVE=false 和 readiness 状态。

- [ ] **Step 3: flags 在服务端生效**

被关闭的业务能力返回结构化 FEATURE_DISABLED；硬门不能被 flag 关闭。前端导航依据服务端 status 隐藏或标记未启用，不再接受 URL/localStorage 越权。

- [ ] **Step 4: 最终统一构建**

合并 V2R-F 后只构建一次 dist，确认 index 引用的所有 hashed assets 存在；重启服务后 /api/health.build_version 与本地 build_version.py 一致。

- [ ] **Step 5: 契约测试**

~~~powershell
& $py -m pytest tests/test_readiness_v2.py tests/test_openapi_contract_v2.py tests/test_platform_config.py -q
& $py -m ruff check web/backend_app.py ab_screener/api/app_factory.py ab_screener/api/routers/readiness.py ab_screener/application/platform_config.py ab_screener/domain/readiness.py
& $py -m mypy web/backend_app.py ab_screener/api/app_factory.py ab_screener/api/routers/readiness.py ab_screener/application/platform_config.py ab_screener/domain/readiness.py
npm --prefix web/frontend run test
npm --prefix web/frontend run build
~~~

- [ ] **Step 6: 文档建议而非越权改状态**

Agent 可更新 AGENTS.md 的路径/Python/已完成架构，但不得修改 docs/STATUS.md、docs/RESEARCH-ROADMAP.md、tasks/backlog.yaml、tasks/implementation_state.yaml。对这些文件的建议放入 handoff，由管理者验收后处理。

---

### Task 12: V2R-P8 — 管理者独立验收

**Files:**
- Create: docs/ACCEPTANCE-V2-REMEDIATION-FINAL.md
- Update only after evidence: tasks/v2_remediation_board_20260823.yaml
- Reconcile only after evidence: tasks/backlog.yaml and tasks/implementation_state.yaml
- Merge user changes carefully: docs/STATUS.md and docs/RESEARCH-ROADMAP.md

- [ ] **Step 1: 身份**

工作区干净；当前 HEAD、config hash、DB fingerprint、前端 build、真实数据门禁和研究报告属于同一身份。

- [ ] **Step 2: 七闸门**

逐项复跑 D/R/S/P/L/O/G。R 允许失败，但只有 D/S/P/L/O/G 全 PASS 才可 ENGINEERING_READY_RESEARCH_BLOCKED。

- [ ] **Step 3: 运行态**

/api/health、/api/v2/system/health、/api/v2/readiness、/api/release/readiness、OpenAPI、四个新增 v2 页面、纸面历史买入闭环和对账均现场验证。

- [ ] **Step 4: 真实时间条件**

至少 7 份符合策略的异位置备份、一次严格恢复、五个真实交易日 soak。时间条件未满足就继续 BLOCKED，不允许补写历史。

- [ ] **Step 5: 最终命令**

~~~powershell
& $py scripts/check_architecture.py --strict
& $py -m ruff check . --exclude web/frontend/node_modules
& $py -m mypy ab_screener paper_trading logic_platform web/backend_app.py
& $py -m pytest -q
& $py -m paper_trading.real_data_gate --days 730 --report runtime/gates
npm --prefix web/frontend run test
npm --prefix web/frontend run build
npm --prefix web/frontend run test:e2e
~~~

- [ ] **Step 6: 状态裁决**

只有三种合法输出：

- BLOCKED：任一硬门、身份、质量、恢复或治理未过。
- ENGINEERING_READY_RESEARCH_BLOCKED：除研究 R 外全部当前身份 PASS。
- PERSONAL_INSTITUTIONAL_READY：七门全部当前身份 PASS；仍不等于允许真实下单。

## 4. Agent 回传协议

每个 Agent 必须复制 docs/handoffs/V2-REMEDIATION-AGENT-HANDOFF-TEMPLATE.md 的结构到自己的固定 handoff 文件，附：

1. base/head SHA；
2. git diff --name-only 和 --stat；
3. 修改前失败、修改后通过证据；
4. DB 是否为副本；
5. API/schema/config 变化；
6. 回滚方案；
7. 未解决阻断；
8. 未宣布 PERSONAL_INSTITUTIONAL_READY 的声明。

管理者收到后执行：

1. 文件所有权检查；
2. 代码与未来函数检查；
3. 定向测试复跑；
4. 交叉域测试；
5. 合并到 integration 分支；
6. 更新任务板为 accepted 或 rework_required；
7. 只有整波接受后才释放下一波任务。

## 5. 自检

- 计划覆盖本次审计的两项 Pytest 失败、115 Ruff、Mypy、前端测试缺失、PIT/公司行为、执行/风险未接线、信号/DAG 空表、健康接口超时、备份/恢复/soak、研究证据、信息增强、flags/readiness 和台账分叉。
- 不允许实现 Agent 修改共享入口或最终状态。
- 不允许通过调阈值、跳测试、放宽 lint、伪造时间或打开真实交易制造通过。
- 所有任务都有固定文件、依赖、测试命令、交付文件和回滚说明要求。
