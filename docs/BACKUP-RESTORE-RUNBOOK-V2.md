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
create_backup(db, backup_root, compressed=True)
```

- SQLite **online backup**（`src.backup(dst)`）→ 临时文件 → 全表确定性 SHA-256、
  `PRAGMA integrity_check` 和 `foreign_key_check` → gzip 压缩 → 通过后原子命名
  `backup_<stamp>.db.gz`（同秒冲突加序号）。
- 每份有效备份必须有同名 `.manifest.json`，清单自身带 SHA-256，并记录归档哈希、
  逻辑库大小、全表哈希、验证时间和工具版本。只有清单有效的文件才计入 7 份。
- 校验失败 → 丢弃临时文件并抛错，**不更新 last good**。
- `verify_backup(path)` 可给已有 `.db` 全量校验并补清单；未经验证的历史裸文件不计数。
- `prune_old_backups(keep=7)`：只清理经过验证的旧文件，绝不删除裸历史文件或唯一备份。
- 健康检查 `backup_ok()`：≥7 份且最近 <24h → ok。

## 3. 恢复演练（`scripts/restore_backup.ps1`）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\restore_backup.ps1 `
  -BackupFile E:\ab-backups\backup_20260827_170000.db.gz `
  -RestoreTo E:\ab-restore-drill\stock_data.db -DryRun
# 确认目标是全新临时路径后去掉 -DryRun
```

- 只接受绝对路径和带有效清单的备份；目标必须不存在，禁止覆盖生产库或旧演练库。
- 恢复后自动执行归档 SHA-256、`integrity_check`、外键和全表内容哈希复验；
  任一不符即删除 partial 文件并非零退出。
- 计时输出供 RTO 记录（目标 ≤1800s）。

## 4. 系统健康（`ab_screener/operations/health.py`）

`system_health(db, backup_root)` 聚合：DB 完整性/WAL/磁盘（>1GB 空闲）/备份状态/端口身份；
任何关键项 FAIL → overall FAIL。API：`GET /api/v2/system/health`、`GET /api/v2/system/backups`。

## 5. Soak 证据（`scripts/soak_monitor_v2.py`）

```text
.venv312\Scripts\python.exe scripts\run_eod_v2.py `
  --db E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db `
  --anchor-dir E:\ab-backups\audit-anchors `
  --signing-key-file E:\ab-backups\security\audit-signing.key `
  --soak-dir runtime\v2\soak --backup-root E:\ab-backups
```

- 运维命令只接受本地数据库最新交易日、当前构建成功扫描；串行执行 DAG、风险快照、
  对账、COMPLETE 清单、审计外部锚点、soak 和备份，任何一步失败即停止。
- 证据写入 `runtime/v2/soak/<trade_date>.json`（DAG COMPLETED 且 manifest COMPLETE 才计数）。
- **不足 5 个不同完成交易日 → O-12 固定 INSUFFICIENT**（不伪造等待结果）。

## 6. 演练脚本

1. 配置 `AB_BACKUP_ROOT`（独立卷/同步目录）并确认可写；
2. 每个真实交易日使用 `run_eod_v2.py` 完成日清；首次创建密钥时才显式增加
   `--initialize-signing-key`；
3. 连续保留 7 份验证备份；历史 `.db` 只有在 `verify_backup()` 全量通过后才计数；
4. 每周执行一次恢复演练（`restore_backup.ps1 -DryRun` 预览 → 全新临时路径实跑）；
5. 检查 `system_health`、`backup_ok` 与 `/api/v2/readiness`；
6. 记录 RPO（备份间隔 ≤1 交易日）与 RTO（恢复耗时 ≤30 分钟）证据。

## 7. 故障处理

| 症状 | 处理 |
|---|---|
| 磁盘不足 | 不更新 last good；先扩容或迁移备份卷 |
| 备份/清单 hash 不符 | 拒绝计数和恢复，丢弃新临时产物；保留上一份 last good |
| 唯一备份将删除 | `prune_old_backups` 显式保护（len<=1 时停止） |
| 恢复后完整性失败 | partial 自动删除；生产库从未被覆盖，无需回滚 |
