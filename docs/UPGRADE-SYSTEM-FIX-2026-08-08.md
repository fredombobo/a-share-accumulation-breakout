# 验收不通过项纠错记录（2026-08-08 10:26 之后）

> 本文是纠错中期记录，已由 `docs/FINAL-ACCEPTANCE-2026-08-08.md` 取代。

依据验收阻断清单逐项修复。仓库：`E:\CODEX\Stock_selection\accumulation_breakout`。

## 已修复（代码）

| 阻断 | 修复 |
|------|------|
| Parquet 只校验首尾日期 | `parquet_cache.py`：区间内**每个**交易日 `content_sha256` 参与 key；中间日变更必 miss |
| input_hash 只哈希前 50 码 | `input_hash_for_scan`：全量排序代码 `sha256`，不同 60 股集合哈希必不同 |
| 取消后可改写 SUCCEEDED | `ScanJobStore.finish`：终态不可改写；`cancel_requested` 时 SUCCEEDED→CANCELLED |
| requeue_stale 无视 stale_seconds | 仅当 heartbeat/started_at **早于** cutoff 才 requeue；CANCELLING 超时→CANCELLED |
| 错误测试固化「取消后成功」 | `test_upgrade_system.py` 改为断言取消后 finish(SUCCEEDED) 落 CANCELLED |
| 无 `/api/scan/runs` | `backend_app` 新增 `GET /api/scan/runs`、`GET /api/scan/runs/{run_id}` |
| INSERT OR REPLACE 双写 job | 改为 `upsert_running`（禁止覆盖终态） |
| 成功扫描不写 scan_runs | 成功后 `_persist_scan_run` 写 `scan_runs` + 漏斗摘要 |
| health 伪 v2 | `scanner_engine=subprocess_v2`，`pickle_read_enabled=false` |
| 成本/基线未接入 | Lab 完成时跑 random/ma 基线 + `promotion_checks` |
| mypy backend 1582 | `pd.Series(...).items()` 类型修正 |
| ECharts >800KB | 按需 `echarts/core` 注册 → **~671KB raw** |

## 质量门禁（本轮重跑）

| 门禁 | 结果 |
|------|------|
| pytest | **164 passed** |
| ruff ab_screener | pass |
| mypy ab_screener + backend_app | **Success** |
| frontend build | pass；EChart chunk **&lt;800KB** |

## 仍未关闭 / 进行中

| 项 | 状态 |
|----|------|
| 历史 720+ 日 / mode=full | Token 已可用；已后台启动 `sync_history.py --days 730`（pid 见 `runtime/sync_history.pid`）。完成后跑 `research_status.py` |
| backend_app/run_screener 行数 ≤200 | 未完成（兼容壳仍大）；功能门禁优先 |
| StrategyLab 去 `Record&lt;unknown&gt;` | 未完成本轮 |
| 全市场 160 日 &lt;120s | 未达标（瓶颈在 detect）；预筛已向量化，后续需共享内存/增量扫 |
| 8000 端口旧实例 | **未强杀用户进程**；源码版本需用「强制重启后端.bat」管理员重启后生效 |
| Git 提交 | 仍由用户决定；本轮仅改源码 |

## 验证建议（用户侧）

```powershell
cd E:\CODEX\Stock_selection\accumulation_breakout
# 管理员重启 8000 后端加载新 dist/源码
# 查历史扩容
Get-Content runtime\sync_history_upgrade.out.log -Tail 20
python research_status.py
# 新 API
curl -s http://127.0.0.1:8000/api/scan/runs
curl -s http://127.0.0.1:8000/api/health
```

## 结论

- 验收列出的 **确定性代码缺陷** 已按项修复并加回归测试。  
- **不能**因 403 日数据将整体标为「更新完成 / full 可晋级」；历史扩容完成后才可重开 full 门禁。  
- 用户当前 **8000 旧 PID** 必须自行/管理员重启后才会看到修复。
