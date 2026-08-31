# 验收标准：NTM×AB 整合 P1+P2 · 2026-08-22

> 本文档是**最终检查者（reviewer）**逐项核对与实施 agent 自检的唯一依据。
> 对应计划：`docs/superpowers/plans/2026-08-22-ntm-p1-overlay.md`、`…-ntm-p2-wiring.md`
> 结论三选一：**ACCEPTED**（全部 G 项 PASS）/ **BLOCKED**（写明未过项与证据）/ 打回修正。

## G1 · P1 文件与注册

| 项 | 判据 | 证据 |
|---|---|---|
| G1.1 | `contracts.py` 的 `OverlayInput` 新增 `national_team: dict \| None = None`，位于末尾、带默认值，其它字段未动 | diff |
| G1.2 | `ab_screener/regimes/national_team_overlay_v1.py` 存在；模块导入即注册；`regime_overlays()` 含 `national_team_overlay_v1` 且 `defensive_overlay_v1` 仍在 | 测试 T7/T8 输出 |
| G1.3 | `regimes/__init__.py` 导出方式与 defensive 一致；`configs/regimes/national_team_overlay_v1.yaml` 存在且可被既有配置加载器解析 | diff + 解析结果 |
| G1.4 | `integrations/ntm_client.py` 只 import json/os/pathlib/datetime/typing | 代码走查 |

## G2 · overlay 行为矩阵（P1 计划 §8.1 T1-T8）

| 输入 | 必须输出 |
|---|---|
| 危险共振（红4绿1） | `allow_new_entries=False`，`mode="defensive"`，reason 含「危险共振」 |
| 机会共振 | `allow_new_entries=True`，`mode="neutral"`（**出现 aggressive 即打回**） |
| 中性 | allow=True，mode=neutral |
| national_team=None / 空 dict | allow=True，reason 含「无信号」 |
| 未知 verdict | 按中性处理（allow=True） |
| override=False + 机会共振 | allow=False，reason=「人工覆盖」（覆盖优先） |

判据：8 个测试用例全绿，断言与上表逐字一致。

## G3 · ntm_client 行为矩阵（P1 计划 §8.2 C1-C9）

- 未配置路径 → `snapshot_path==""`，`ntm_status` enabled=False/reachable=False；
- 缺失/损坏/schema≠1/as_of 非法/过期（>5 交易日）→ `read_ntm_snapshot` 返回 **None**，全程不 raise；
- 临界新鲜（=5 交易日）→ 正常返回 dict；
- `is_fresh` 周末跨越、未来日期（as_of>today）→ False；
- `today` 参数可注入。

判据：9 个用例全绿；C6（过期）与 C7（临界）同时 PASS 才算 PIT 正确。

## G4 · 既有回归不受损

- `tests/test_strategy_plugin_contract.py`、`tests/test_desk_supplement.py`、`tests/test_astock_client.py`、`tests/test_openapi_contract_v2.py` 全绿；
- **既有断言零修改**（diff 可证）；
- OpenAPI path 总数不变。

## G5 · P2 汇总执行器

- `tests/test_evaluate_overlays.py` E1-E4 全绿；
- E4（不 patch 注册表，危险共振 → allow=False）证明 P1 overlay 真实进入汇总链路；
- fail-closed：evaluate 抛异常 → 汇总禁止，reason 含「fail-closed」。

## G6 · 接线行为等价与正确性

| 项 | 判据 |
|---|---|
| G6.1 | run_screener 接线位于 `detect_regime` 之后、`allow_new_entries` 消费之前（提供行号）；`regime.allow_new_entries` 为 **AND 合并**，非覆盖 |
| G6.2 | 快照读取每次扫描只读一次，不在候选循环内 |
| G6.3 | `benchmark_trend`/`drawdown_from_peak` 要么复用仓库既有值（注明出处），要么 0.0；无新造计算 |
| G6.4 | `regime.to_dict()` 未新增破坏性键（或新增可选键且旧断言兼容） |
| G6.5 | 未配置快照 → 手工冒烟输出与对照组逐行一致 |

## G7 · desk-supplement 与 health 扩展

- desk-supplement 响应新增 `national_team` 段（enabled/reachable/as_of/verdict/red_count/green_count/holders_count/seat_alerts_count/error），旧字段与旧断言不变；D1-D3 用例绿；
- `/api/health` 响应新增 `national_team` 状态块；health 契约相关测试绿；
- 两个端点读快照合计只读一次文件、不触网、不读库。

## G8 · 门禁与冒烟

- `scripts/check_architecture.py --strict` exit 0；
- 手工冒烟三组（P2 计划 §8）：危险共振 → 禁止开仓（A 池空或 allow=False + 日志含原因）；机会共振 → 正常放行；未配置 → 与现状一致；
- 全量相关测试绿；无新依赖；无 requirements 改动。

## 代码走查清单（reviewer 逐条）

1. `national_team_overlay_v1.evaluate` 为纯函数：无 os/网络/文件/库读取；
2. `ntm_client` 无 sqlite3/subprocess/requests；所有异常路径返回 None/False；
3. overlay 的 `mode` 语义正确（机会共振=neutral）；
4. 注册唯一、无重复注册点；
5. 新文件 UTF-8、中文注释与计划一致；
6. PIT 判定只数工作日；
7. 无改动越界（未碰 NTM 仓库、paper 账本、前端、新 API 路径、既有测试断言）。

## 结论模板

```
[ACCEPTED|BLOCKED] NTM-P1P2-2026-08-22
- G1: PASS（证据…）
- G2: PASS（8/8）
- …
- 走查: PASS
- 备注: …
```

任何一项 FAIL → 打回并列出「哪项、差在哪、修复要求」；不得以"大致通过"放行。
