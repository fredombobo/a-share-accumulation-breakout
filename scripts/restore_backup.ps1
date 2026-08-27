# restore_backup.ps1 - verified v2 backup restore drill (RTO target <= 30 min)
#
# Safety: the Python implementation accepts only manifest-backed backups and
# refuses to overwrite an existing target. DryRun performs no filesystem write.

param(
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [string]$RestoreTo = "",
    [string]$Report = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not [System.IO.Path]::IsPathRooted($BackupRoot)) {
    Write-Error "BackupRoot must be an absolute path"
    exit 2
}

if ($RestoreTo -and -not [System.IO.Path]::IsPathRooted($RestoreTo)) {
    Write-Error "RestoreTo must be an absolute path"
    exit 2
}

if ($DryRun -and -not $RestoreTo) {
    $RestoreTo = Join-Path $env:TEMP (
        "ab-restore-drill-" + (Get-Date -Format "yyyyMMdd_HHmmss") + "\stock_data.db"
    )
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = $env:AB_PYTHON
if (-not $Python) {
    $LocalPython = Join-Path $RepoRoot ".venv312\Scripts\python.exe"
    if (Test-Path $LocalPython) {
        $Python = $LocalPython
    } else {
        $Python = "python"
    }
}

$ArgsList = @(
    (Join-Path $PSScriptRoot "restore_backup_v2.py"),
    "--backup-root", $BackupRoot,
    "--restore-to", $RestoreTo
)
if ($DryRun) {
    $ArgsList += "--dry-run"
    Write-Host "[DRY-RUN] preview only; no restore will be performed"
}
if ($Report) {
    $ArgsList += @("--report", $Report)
}

Write-Host ("restore source root: {0}" -f $BackupRoot)
Write-Host ("restore target: {0}" -f $RestoreTo)
Write-Host "readonly checks: manifest/archive SHA-256, integrity, foreign keys, table hashes"
Write-Host "safety: verified backups only; existing target and production overwrite are rejected"

& $Python @ArgsList
exit $LASTEXITCODE
