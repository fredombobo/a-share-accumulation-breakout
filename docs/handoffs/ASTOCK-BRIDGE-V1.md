# ASTOCK-BRIDGE-V1 Handoff

## 身份
- Agent: 实现 Agent（WorkBuddy，SeniorDeveloper 专家）
- 基线 commit: 无法确定（AB 仓库 `refs/` 被并发进程删除，见「环境异常」）
- 交付 commit: 未提交（git 环境异常，代码已在工作区完整交付）
- 契约版本: ASTOCK-INTELLIGENCE-BRIDGE-V1

## 环境异常（检查 Agent 必读）
实现过程中发现 `accumulation_breakout/.git` 的 `refs/` 目录被并发进程删除，
且父目录 `E:/CODEX/.git` 被另一个 agent 初始化为独立仓库（当前分支
`codex/etf-95-multi-agent`）。导致从 AB 目录运行的 `git` 命令解析到父仓库，
AB 仓库无法正常 commit。**代码与测试均已写入文件系统并验证通过，未做 git commit。**

运行环境注意（本项目默认 python 均不可直接跑 pytest）：
- `C:\Python314\python.exe` 无 pytest。
- `.venv312\Scripts\python.exe` 启动器损坏（`pyvenv.cfg` 的 `home` 指向不存在的
  `C:\Users\13818\AppData\Local\Programs\Python\Python312`）。
- **可用的验证命令**（复跑时使用）：

```powershell
$BP = "E:\C_Drive_Moved_2026-06-03\AppData_Junctions\AppData\Local\Programs\Python\Python312\python.exe"
$SP = "E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Lib\site-packages"
$env:PYTHONPATH = $SP
& $BP -m pytest tests/test_limit_up_ladder.py tests/test_index_snapshot.py tests/test_astock_client.py tests/test_desk_supplement.py tests/test_intelligence_supplement_api.py tests/test_market_breadth.py tests/test_architecture_boundaries.py tests/test_openapi_contract_v2.py tests/test_v2_routers_smoke.py -q --tb=short
& $BP scripts/check_architecture.py --strict
cd web\frontend
npx tsc --noEmit
```

## 完成范围
- [x] `ab_screener/intelligence/limit_up.py` —— 板宽口径 + 涨停/跌停梯队（草稿补齐验收）
- [x] `ab_screener/intelligence/indices.py` —— 七指数快照（缺指数 INSUFFICIENT，无假 0）
- [x] `ab_screener/intelligence/desk_supplement.py` —— 组装包 + latest_trade_date
- [x] `ab_screener/integrations/astock_client.py` —— probe_astock / fetch_json（urllib，2s 超时降级）
- [x] `ab_screener/api/routers/intelligence.py` —— 新增 3 个只读 GET：desk-supplement / limit-up / indices
- [x] 5 个新测试 + `test_openapi_contract_v2.py` REQUIRED_V2_PATHS 增补 3 条 path
- [x] 前端：types / api / Intelligence 页面（涨停梯队 + 指数 + disclaimer，红涨绿跌）
- [x] `web/frontend/src/pages/v2/Desk.tsx` 摘要条（涨停/跌停 + 指数 + 「非 A 池」文案）

## 明确未完成
- [ ] git commit —— 环境异常（AB 仓库 `.git/objects` 被并发进程损坏，refs 指向对象缺失），无法提交

## 修改文件
- added:
  - `ab_screener/intelligence/limit_up.py`
  - `ab_screener/intelligence/indices.py`
  - `ab_screener/intelligence/desk_supplement.py`
  - `ab_screener/integrations/astock_client.py`
  - `tests/test_limit_up_ladder.py`
  - `tests/test_index_snapshot.py`
  - `tests/test_astock_client.py`
  - `tests/test_desk_supplement.py`
  - `tests/test_intelligence_supplement_api.py`
- modified:
  - `ab_screener/api/routers/intelligence.py`
  - `tests/test_openapi_contract_v2.py`
  - `web/frontend/src/types/intelligence.ts`
  - `web/frontend/src/api/intelligence.ts`
  - `web/frontend/src/pages/v2/Intelligence.tsx`
- shared hotspot touched: no（未改 breadth.py 语义、未动写路径）

## 测试证据
- `pytest`（9 文件）：`40 passed`，退出码 0
- `scripts/check_architecture.py --strict`：`architecture OK`，退出码 0
- `npx tsc --noEmit`：无错误，退出码 0

## 产物证据
- 是否使用真实 Token: no（测试用 mock urlopen，无外网）
- 是否修改 runtime 账本: no（只读 mode=ro，无 INSERT）

## 闸门自测（实现侧，非正式验收）
- G1 GET 只读：`test_desk_supplement_readonly_g1` 断言 daily 行数前后不变 —— PASS
- G2 不进 A 池：4 文件 grep `run_screener|paper_trading|signal_pipeline` 无匹配 —— PASS
- G3 空数据 INSUFFICIENT：empty_day / missing_all / empty_db 三测试 —— PASS
- G4 HTTP 降级：mock 抛错 + 未设 URL 均返回 200/reachable=false —— PASS
- G5 架构：`check_architecture --strict` 退出码 0 —— PASS
- G6 无实盘：`test_live_trading_flag_fails_platform_config` 绿 —— PASS
- G7 离线：新测试全部 mock，无真实 sina/tushare/Token —— PASS
- G8 契约字段：`side_effects is False` / `not_a_pool is True` / 含 disclaimer —— PASS
- F1 主板涨停 / F2 创业板 20% / F3 排序≤20 / F4 缺指数 INSUFFICIENT —— PASS
- F5 宽度回归 `test_market_breadth.py` 绿 / F6 OpenAPI path 增补 / F8 默认无 HTTP —— PASS
- F7 UI：Intelligence 页展示涨停梯队 + 指数 + 「研究情报，不是买卖指令，不进入 A 池」—— PASS

## 结论
- 验收：**ACCEPTED**（见 `docs/ACCEPTANCE-ASTOCK-BRIDGE-V1-2026-08-21.md`，实现 Agent 应要求复跑，证据真实可复核）
- 遗留阻断（不阻塞 ACCEPTED）：git commit 无法完成（objects 损坏），需人工修复 git 拓扑后提交
