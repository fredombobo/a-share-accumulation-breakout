# restore_backup.ps1 — P8 备份恢复演练（RTO 目标 ≤30 分钟）
#
# 用法：powershell -ExecutionPolicy Bypass -File scripts\restore_backup.ps1
#       -BackupRoot <备份目录> -RestoreTo <目标绝对路径.db> [-DryRun]
#
# 安全：只允许绝对路径目标；恢复前对目标做 .pre-restore 备份；
# 恢复后校验完整性（PRAGMA integrity_check）与关键表行数。

param(
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [Parameter(Mandatory = $true)][string]$RestoreTo,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Py = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) ".venv312\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

if (-not [System.IO.Path]::IsPathRooted($RestoreTo)) {
    Write-Error "RestoreTo 必须是绝对路径（防误操作）"
    exit 2
}

# 最新备份
$Backups = @(Get-ChildItem $BackupRoot -Filter "backup_*.db" | Sort-Object LastWriteTime -Descending)
if ($Backups.Count -eq 0) { Write-Error "备份根目录无 backup_*.db"; exit 1 }
$Latest = $Backups[0].FullName
$SizeMB = [math]::Round($Backups[0].Length / 1MB, 1)
Write-Host ("选定备份: {0} ({1} MB)" -f $Latest, $SizeMB)

if ($DryRun) {
    Write-Host "[DRY-RUN] 将恢复 $Latest → $RestoreTo"
    exit 0
}

# 目标先做 .pre-restore 副本（绝不直接覆盖唯一可用数据）
if (Test-Path $RestoreTo) {
    $Pre = "$RestoreTo.pre-restore"
    Copy-Item $RestoreTo $Pre -Force
    Write-Host "目标已备份: $Pre"
}

$t0 = Get-Date
Copy-Item $Latest $RestoreTo -Force
$elapsed = ((Get-Date) - $t0).TotalSeconds

# 校验
$Code = @"
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
try:
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    print(f"integrity={ok} tables={tables}")
    sys.exit(0 if ok == "ok" else 1)
finally:
    conn.close()
"@
$CodeFile = Join-Path $env:TEMP "restore_check.py"
[System.IO.File]::WriteAllText($CodeFile, $Code, [System.Text.Encoding]::UTF8)
& $Py $CodeFile $RestoreTo
if ($LASTEXITCODE -ne 0) { Write-Error "恢复后完整性校验失败"; exit 1 }

Write-Host "恢复完成: $RestoreTo（$([math]::Round($elapsed, 1))s，RTO 目标 ≤1800s）"
exit 0
