# restore_backup.ps1 - P8 backup restore drill (RTO target <= 30 min)
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restore_backup.ps1
#        -BackupRoot <backup dir> -RestoreTo <absolute target.db> [-DryRun]
#
# Safety: absolute path target only; pre-restore copy before overwrite;
# integrity check (PRAGMA integrity_check) + table count after restore.

param(
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [string]$RestoreTo = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not [System.IO.Path]::IsPathRooted($BackupRoot)) {
    Write-Error "BackupRoot must be an absolute path"
    exit 2
}

# Latest backup
$Backups = @(Get-ChildItem $BackupRoot -Filter "backup_*.db" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
if ($Backups.Count -eq 0) {
    Write-Error "No backup_*.db found under: $BackupRoot"
    exit 1
}
$Latest = $Backups[0].FullName
$SizeMB = [math]::Round($Backups[0].Length / 1MB, 1)
Write-Host ("[DRY-RUN] source backup: {0} ({1} MB)" -f $Latest, $SizeMB)

# DryRun without an explicit target: derive a safe absolute temp target
# (never inside the repo, never equal to the production db, never copied to).
if ($DryRun -and -not $RestoreTo) {
    $ProdDb = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "runtime\stock_data.db"
    $RestoreTo = Join-Path $env:TEMP ("restore_drill_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".db")
    $targetFull = [System.IO.Path]::GetFullPath($RestoreTo)
    $prodFull = [System.IO.Path]::GetFullPath($ProdDb)
    if ($targetFull -ieq $prodFull) {
        Write-Error "derived temp target equals production db: $targetFull"
        exit 2
    }
    Write-Host ("[DRY-RUN] derived safe temp target: {0}" -f $RestoreTo)
    Write-Host ("[DRY-RUN] target != production db ({0})" -f $prodFull)
}

Write-Host ("[DRY-RUN] restore target: {0}" -f $RestoreTo)
Write-Host "[DRY-RUN] readonly check: PRAGMA integrity_check + table count after restore"
Write-Host "[DRY-RUN] safety: will not overwrite production db; pre-restore copy first"

if ($DryRun) {
    Write-Host "[DRY-RUN] preview only, no restore performed."
    exit 0
}

if (-not $RestoreTo -or -not [System.IO.Path]::IsPathRooted($RestoreTo)) {
    Write-Error "RestoreTo must be an absolute path (required for actual restore)"
    exit 2
}

# Pre-restore copy of existing target (never overwrite sole usable data directly)
if (Test-Path $RestoreTo) {
    $Pre = "$RestoreTo.pre-restore"
    Copy-Item $RestoreTo $Pre -Force
    Write-Host "target backed up: $Pre"
}

$t0 = Get-Date
Copy-Item $Latest $RestoreTo -Force
$elapsed = ((Get-Date) - $t0).TotalSeconds

# Integrity verification
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
$Py = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) ".venv312\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
& $Py $CodeFile $RestoreTo
if ($LASTEXITCODE -ne 0) { Write-Error "integrity check failed after restore"; exit 1 }

Write-Host ("restore complete: {0} ({1}s, RTO target <= 1800s)" -f $RestoreTo, [math]::Round($elapsed, 1))
exit 0
