# HANDOFF：NTM×AB 整合（P1+P2）· 2026-08-22

> 给实施 agent 的开工指引。任务范围：P1（overlay + ntm_client + 测试）→ P2（接线 + 回归）。
> 计划文档（步骤级、含代码规格，按此执行）：
> - `docs/superpowers/plans/2026-08-22-ntm-p1-overlay.md`
> - `docs/superpowers/plans/2026-08-22-ntm-p2-wiring.md`
> 验收标准（最终检查依据）：
> - `docs/ACCEPTANCE-NTM-P1P2-2026-08-22.md`
> 总设计：`E:\CODEX\national-team-monitor\docs\INTEGRATION-PLAN.md`

## 1. 背景（一分钟版）

- 本地有 4 个 A 股系统：NTM（国家队监控，`E:\CODEX\national-team-monitor`）、accumulation_breakout（本仓库，横盘吸筹突破 + 纸面交易）、astock（看板）、chip-selector（筹码）。
- 本次任务：把 **NTM 的「国家队五灯共振」信号接入 AB 的开仓门控**。
- NTM 已完成 P0：`python cli.py snapshot --fetch --out <path>` 输出 `snapshot.json`（契约见 P1 计划 §1）。NTM 侧 31 个单测全绿。
- 本仓库 P1/P2 完成后，效果：NTM 危险共振日 → AB 禁止新开仓；机会共振日 → 放行（但不进攻）；未配置快照 → 行为与现状**完全一致**。

## 2. 关键决策（不许偏离）

| 决策 | 内容 |
|---|---|
| A2 | 机会共振 → **仅放行**：`allow_new_entries=True, mode="neutral"`（绝不置 aggressive） |
| PIT | 快照 `as_of` 按交易日滞后 >5 天 → 过期 → 按「无信号」处理 |
| 降级 | 快照未配置/缺失/损坏/过期 → 所有新逻辑零影响（等价现状） |
| fail-closed | overlay 汇总：任一 overlay 禁止 → 禁止；overlay 抛异常 → 按禁止处理 |
| 边界 | 不新增 API 路径（仅 desk-supplement/health 字段扩展）；不改写路径；不改 NTM 仓库；不改既有测试断言；不引入新依赖 |

## 3. 仓库与环境

- AB 根目录：`E:\CODEX\Stock_selection\accumulation_breakout`（本 handoff 所在仓库）
- NTM 根目录：`E:\CODEX\national-team-monitor`（只读参考，**不要改**）
- Python：依次探测 `C:\Python314\python.exe` → `C:\Python312\python.exe` → `C:\Users\13818\anaconda3\python.exe`，用**能跑绿最小测试集**的那个；开工前清理代理污染：
  ```powershell
  Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
  $env:HTTP_PROXY=$env:HTTPS_PROXY=$env:ALL_PROXY=$null
  $env:http_proxy=$env:https_proxy=$env:all_proxy=$null
  ```
- Tushare 硬约束（AB AGENTS.md）：只从 `tushare_init.py` 取 pro、curl_cffi。**本次任务不涉及任何 Tushare 调用**，遵守即可，不要顺手改。
- 架构门禁：`<PY> scripts/check_architecture.py --strict` 必须 exit 0；API 层禁止 import sqlite3/subprocess（本次新文件全部不涉及）。

## 4. 实施顺序

1. **P1**（先做完、全绿、自己先验收）：
   - `contracts.py` 加字段 → `regimes/national_team_overlay_v1.py` → `regimes/__init__.py` 导出 → `configs/regimes/national_team_overlay_v1.yaml` → `integrations/ntm_client.py` → 两个测试文件 → 回归（P1 计划 §9）。
2. **P2**（P1 验收通过后）：
   - Step 0 勘察（先记录三问答案与行号）→ `evaluate_overlays.py` → run_screener 接线 → desk_supplement 扩展 → health 扩展 → 测试 → 回归 + 手工冒烟（P2 计划 §6-§8）。

## 5. 常见坑（前人踩过）

1. **frozen dataclass**：新字段必须放 `OverlayInput` 末尾且带默认值，否则既有构造点全炸。
2. **注册表重复**：overlay 模块导入即注册，`register_regime_overlay` 对重复 id 抛 `OverlayRegistryError` —— 不要在多个文件重复注册；测试里 import 模块即可验证注册。
3. **导入副作用**：`ab_screener/regimes/__init__.py` 导入新模块会连带注册；确认与 defensive 的导入方式一致。
4. **PIT 新鲜度**：`is_fresh` 数的是**工作日**（跳过周末），不是自然日；`today` 必须可注入以便测试。
5. **编码**：所有新文件 UTF-8；JSON 读取用 `encoding="utf-8"`；PowerShell 输出中文乱码是显示问题，不是文件问题。
6. **行为等价**：`regime.allow_new_entries = regime.allow_new_entries and overlay_dec.allow_new_entries` —— 未配置快照时 overlay_dec 恒 True，结果逐位等于现状。别改成「覆盖」语义。
7. **OpenAPI**：本任务不加新 path；desk-supplement/health 加字段不改变 path 集合，`test_openapi_contract_v2.py` 必须仍绿。
8. **别改旧测试断言**：如需断言新字段，**新增**用例，不动旧断言。

## 6. 完成标准（返回给检查者的证据包）

按顺序打包（缺一项即打回）：

1. 所用解释器路径 + Step 0/P2 Step 0 勘察结论；
2. 改动文件清单（P1：6 文件；P2：≤6 文件）+ 每文件 diff 摘要；
3. P1 计划 §9 与 P2 计划 §7 的**完整命令输出**（含 PASS/FAIL 行与 exit code）；
4. P2 手工冒烟三组输出（危险共振 / 机会共振 / 未配置 对比）；
5. `check_architecture.py --strict` 输出 `exit 0` 证据。

## 7. 明确不做（超出范围即拒绝）

- astock、chip-selector 的整合（那是后续 Phase，另有计划）；
- NTM 仓库任何改动；
- 新 API 路由、前端页面；
- paper 账本 / 扫描写路径 / 数据库 schema 变更；
- 依赖升级、requirements 改动。
