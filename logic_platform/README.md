# logic_platform · 量价预测逻辑生成平台

挂在 `accumulation_breakout` 上的扩展包。**规格与分期计划见主文档：**

> `docs/VOLUME-PRICE-LOGIC-PLATFORM.md`

## 当前状态（2026-08-08）

- ✅ Phase 0：包骨架、配置、888 data_lake 只读桥、SQLite 迁移（版本段 101+）、`/api/logic/health`
- ✅ Phase 1：价格/量能特征、状态机（IDLE→ACCUMULATION→TIGHTENING→BREAKOUT→FOLLOW_THROUGH→FAIL）、`/api/logic/features/{code}`、`/api/logic/explain/{code}`、CLI 结构扫描
- ⏳ Phase 2：预测服务（未开始）
- ⏳ Phase 3：DSL + 模板 + 回测闸门（未开始）

验收报告：`docs/LOGIC-PLATFORM-PHASE1-ACCEPTANCE-2026-08-08.md`

## 快速上手

```powershell
# 单股解读（研究信号，非买卖建议）
C:\Python314\python.exe -c "from logic_platform.service import explain; import json; print(json.dumps(explain('002793.SZ', __import__('logic_platform.data.ab_store', fromlist=['ABStore']).ABStore()), ensure_ascii=False, indent=2))"

# 全市场结构扫描（前 100 只）
C:\Python314\python.exe -m logic_platform.cli.run_logic_scan --limit 100 --top 10

# API（宿主启动后，宿主端口 8001）
# GET http://localhost:8001/api/logic/health
# GET http://localhost:8001/api/logic/explain/002793.SZ
```

## 硬约束（继承 AGENTS.md §12）

- 唯一箱体计算在宿主 `signals.py`；本包只适配不重算
- 888 data_lake 只读；写操作仅限 AB `runtime/`
- SQLite 每操作新连接；`ON CONFLICT DO UPDATE`；禁 INSERT OR REPLACE
- 默认 research_only；所有生成逻辑必须经 DSL + 闸门后才可进纸交易
