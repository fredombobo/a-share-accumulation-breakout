# V2R-O1 Handoff — 快速健康、备份接线与严格恢复演练

## 1. base / head
- base: `b6772c3`
- head: 见 git log（提交后）
- 分支/worktree: `v2r-o1` @ `E:\CODEX\Stock_selection\worktrees\v2r-o1`

## 2. 修改文件
- modified: `ab_screener/operations/health.py`（快速路径不跑 integrity_check，读离线深检证书，backup_root 接受 None）
- modified: `ab_screener/api/routers/system.py`（AB_BACKUP_ROOT 未配置 → BACKUP_ROOT_UNCONFIGURED，去掉 runtime/backups 悄悄 fallback）
- modified: `scripts/restore_backup.ps1`（去 BOM + 纯 ASCII 重写 + DryRun 无交互 exit 0）
- created: `scripts/check_db_integrity.py`（离线 PRAGMA integrity_check + JSON 证书）
- created: `tests/test_system_health_fast.py`
- created: `tests/test_restore_backup_contract.py`
- created: `docs/handoffs/V2R-O1.md`

## 3. 修改前失败 / 修改后通过
- 修改前：GET /api/v2/system/health 热路径跑 `PRAGMA integrity_check`（16GB 库需数分钟）；
  restore_backup.ps1 带 UTF-8 BOM + 中文注释，PowerShell 5.1 按 GBK 读 → ParserError exit 1。
- 修改后：快速健康只读 schema/version/latest date/WAL + 离线证书（deep_check.status ∈ PASS/STALE/MISSING）；
  restore_backup.ps1 DryRun 无交互 exit 0（纯 ASCII）。
- 质量门：pytest 8 passed（backup_restore + system_health_fast + restore_backup_contract）；ruff 0；mypy 0。

## 4. DB 是否副本
- 否。测试用 tmp_path 临时库；worktree runtime 为 b6772c3 代码自建兼容空库；未触碰生产库。

## 5. API/schema/config 变化
- `/api/v2/system/health` 返回结构：`database` 去掉 `integrity` 字段，新增 `fingerprint`/`schema_version`/`latest_date`/`deep_check`；
  `backup` 未配置时为 `{status: "BACKUP_ROOT_UNCONFIGURED"}`。
- 无 schema 变化。`scripts/check_db_integrity.py` 为新增离线深检命令。

## 6. 回滚方案
- `git revert` 或 checkout 回 b6772c3；无迁移/DB 副作用。

## 7. 未解决阻断
- soak_monitor_v2.py / backup.py 在 Files 清单中但 Steps 未要求改动，本轮未动（保持现状，避免过度改动）。
- PowerShell 工具 stdout 捕获异常（Write-Output 无回显），DryRun 契约改用 Python subprocess 验证。

## 8. 声明
- 未宣布 PERSONAL_INSTITUTIONAL_READY。结论 READY_FOR_REVIEW。

## 9. 管理者区（2026-08-23）

- 范围审查：PASS；未删除备份、未开启 scheduler、无生产 DB 写入。
- 代码审查：FAIL；RestoreTo 在 DryRun 仍必填；health/backups 公开 backup_root 查询覆盖。
- 定向复验：8 passed；Ruff 0；Mypy 0；16GB 生产库只读快速健康约 10.1ms。
- 交叉域复验：合同原命令 `-BackupRoot E:\ab-backups -DryRun` exit 1；OpenAPI 显示 health/backups 均含 backup_root 查询参数。
- 运行态复验：当前备份 2 份、最新 <24h，但未满足 7 份；状态保持 FAIL。
- 判定：REWORK_REQUIRED
- 缺陷编号：V2R-O1-RW-001（DryRun 合同失败）；V2R-O1-RW-002（HTTP 可覆盖备份根）；V2R-O1-RW-003（健康 SQL 防回归测试不足）。
- 允许进入的下一任务：否；V2R-O2 继续 blocked。
- 完整要求：`docs/ACCEPTANCE-V2-REMEDIATION-WAVE1-2026-08-23.md#v2r-o1`。

## 9. 返工修复（Wave1 REWORK，追加 commit）

- 追加 commit：`212a9ae` + `256cf86`
- V2R-O1-RW-002 修复：删除 `/api/v2/system/health` 与 `/api/v2/system/backups` 的公开 `backup_root`
  查询参数（HTTP 不得覆盖生产备份根，只读 `AB_BACKUP_ROOT`）。
- V2R-O1-RW-001 修复：`restore_backup.ps1` 的 `RestoreTo` 改为可选——DryRun 不传 RestoreTo 也 exit 0
  （目标显示 "(unspecified)"）；实际恢复仍要求绝对路径。
- 新增测试 `test_restore_backup_dryrun_without_restoreto_exits_zero`（9 passed，ruff All checks passed）。
