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
