# quality_gate.ps1 — P0.3 本地质量门（与 CI quality job 同构）
#
# 阶段：ruff → mypy → check_architecture → pytest(offline) → 前端 build
# 运行方式：powershell -ExecutionPolicy Bypass -File scripts/quality_gate.ps1
#   -SkipFrontend  跳过前端 build（仅后端改动时提速）
#   -Strict        架构检查按 --strict 执行（存量债务也失败，P5 验收用）
#
# 退出码：全部通过 0；任一阶段失败非 0，并在末尾打印失败阶段清单。

param(
    [switch]$SkipFrontend,
    [switch]$Strict
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# 权威运行时：优先 .venv312（Python 3.12），退回系统 python。
$Py = Join-Path $Root ".venv312\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$Stages = @(
    @{ Name = "ruff"; Cmd = { & $Py -m ruff check $Root --exclude "$Root\web\frontend\node_modules" } },
    @{ Name = "mypy"; Cmd = {
        & $Py -m mypy `
            "$Root\signals.py" "$Root\optimizer.py" "$Root\walkforward.py" `
            "$Root\local_store.py" "$Root\config.py" `
            "$Root\ab_screener\domain\costs.py" `
            "$Root\ab_screener\domain\entry_definition.py" `
            "$Root\ab_screener\domain\entry_definition_v2.py" `
            "$Root\ab_screener\research\backtest_engine.py" `
            "$Root\ab_screener\research\pit_reader.py" `
            "$Root\ab_screener\research\regime_filter.py" `
            "$Root\ab_screener\research\resilient_absorption.py" `
            "$Root\ab_screener\research\trusted_run.py" `
            "$Root\ab_screener\data\benchmark_pit_sync.py" `
            "$Root\paper_trading\rules.py" "$Root\paper_trading\engine.py" `
            "$Root\backtest_custom.py"
    } },
    @{ Name = "check_architecture"; Cmd = {
        $args = @("$Root\scripts\check_architecture.py")
        if ($Strict) { $args += "--strict" }
        & $Py @args
    } },
    @{ Name = "pytest"; Cmd = { & $Py -m pytest "$Root\tests\" -q -k "not browser" } }
)

if (-not $SkipFrontend) {
    $Stages += @{ Name = "frontend_build"; Cmd = {
        Push-Location (Join-Path $Root "web\frontend")
        try { & npm run build } finally { Pop-Location }
    } }
}

$Failed = @()
foreach ($stage in $Stages) {
    Write-Host "`n=== quality_gate: $($stage.Name) ===" -ForegroundColor Cyan
    try {
        & $stage.Cmd
        if ($LASTEXITCODE -ne 0) { throw "stage $($stage.Name) exit=$LASTEXITCODE" }
    } catch {
        Write-Host "FAIL: $($stage.Name) — $($_.Exception.Message)" -ForegroundColor Red
        $Failed += $stage.Name
    }
}

if ($Failed.Count -gt 0) {
    Write-Host "`nquality_gate FAILED stages: $($Failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "`nquality_gate OK: ruff / mypy / check_architecture / pytest / frontend_build 全部通过" -ForegroundColor Green
exit 0
