# BACKUP-RESTORE-RUNBOOK-V2 — 备份与恢复演练手册（P8）

> 对应验收：RPO ≤1 交易日；恢复演练 RTO ≤30 分钟；≥7 份连续备份；最近成功备份 <24h；
> 磁盘不足/损坏/hash 不符不更新 last good，也不删除唯一可用备份。

## 1. 前置条件（O 闸门）

- `AB_BACKUP_ROOT` 必须由用户配置为**不在活动数据库目录内**的第二卷或独立同步目录；
  缺失/不可写 → O 闸门 INSUFFICIENT/FAIL（fail-closed）。
- 七份约 2.5GB+ 数据库加 PIT 增长：**先做空间预算**（`backfill_v2 --preflight` 同口径：
  可用空间 ≥ 2×DB + 预计新增）。
- 保留策略不得删除唯一已验证备份。

## 2. 备份（`ab_screener/operations/backup.py`）

```text
create_backup(db, backup_root)
```

- SQLite **online backup**（`src.backup(dst)`）→ 临时文件 → 关键表 hash 校验
  （`_table_hashes`）→ 通过后**原子命名** `backup_<stamp>.db`（同秒冲突加序号）。
- 校验失败 → 丢弃临时文件并抛错，**不更新 last good**。
- `prune_old_backups(keep=7)`：保留最近 7 份；**绝不删除唯一备份**。
- 健康检查 `backup_ok()`：≥7 份且最近 <24h → ok。

## 3. 恢复演练（`scripts/restore_backup.ps1`）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\restore_backup.ps1 `
  -BackupRoot E:\ab-backups -RestoreTo E:\CODEX\Stock_selection\runtime\stock_data.db -DryRun
# 确认后去掉 -DryRun
```

- 只接受绝对路径目标；恢复前目标先做 `.pre-restore` 副本（不直接覆盖）。
- 恢复后自动 `PRAGMA integrity_check` + 表数校验；失败即报错退出非零。
- 计时输出供 RTO 记录（目标 ≤1800s）。

## 4. 系统健康（`ab_screener/operations/health.py`）

`system_health(db, backup_root)` 聚合：DB 完整性/WAL/磁盘（>1GB 空闲）/备份状态/端口身份；
任何关键项 FAIL → overall FAIL。API：`GET /api/v2/system/health`、`GET /api/v2/system/backups`。

## 5. Soak 证据（`scripts/soak_monitor_v2.py`）

```text
.venv312\Scripts\python.exe scripts\soak_monitor_v2.py --db runtime/stock_data.db --soak-dir runtime/v2/soak --collect 20260818
```

- 证据写入 `runtime/v2/soak/<trade_date>.json`（manifest 状态 COMPLETE 才计数）。
- **不足 5 个不同完成交易日 → O-12 固定 INSUFFICIENT**（不伪造等待结果）。

## 6. 演练脚本

1. 配置 `AB_BACKUP_ROOT`（独立卷/同步目录）并确认可写；
2. 连续 7 个交易日执行日结后运行备份（或按日手动 `create_backup`）；
3. 每周执行一次恢复演练（`restore_backup.ps1 -DryRun` 预览 → 实跑计时）；
4. 检查 `system_health` 与 `backup_ok` 输出；
5. 记录 RPO（备份间隔 ≤1 交易日）与 RTO（恢复耗时 ≤30 分钟）证据。

## 7. 故障处理

| 症状 | 处理 |
|---|---|
| 磁盘不足 | 不更新 last good；先扩容或迁移备份卷 |
| 备份 hash 不符 | 丢弃该临时备份并告警；保留上一份 last good |
| 唯一备份将删除 | `prune_old_backups` 显式保护（len<=1 时停止） |
| 恢复后完整性失败 | 停止使用目标库；用 `.pre-restore` 副本回滚 |
