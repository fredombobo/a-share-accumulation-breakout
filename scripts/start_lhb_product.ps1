param(
  [Parameter(Mandatory = $false)]
  [string]$DbPath = "",
  [Parameter(Mandatory = $false)]
  [int]$Port = 8123
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $DbPath) {
  $DbPath = Join-Path $Root "runtime\lhb_product.db"
}
$ResolvedDb = (Resolve-Path -LiteralPath $DbPath).Path
if (-not [System.IO.Path]::IsPathRooted($ResolvedDb)) {
  throw "DbPath must be absolute"
}
if ($ResolvedDb -eq (Join-Path $Root "runtime\stock_data.db")) {
  throw "Refusing to use production database runtime\stock_data.db"
}

$Py = Join-Path $Root ".venv312\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) {
  throw "Project Python was not found: $Py"
}

Write-Host "LHB research product: http://127.0.0.1:$Port/v2/lhb/radar" -ForegroundColor Green
Write-Host "Database copy: $ResolvedDb"
Write-Host "Press Ctrl+C to stop; trading and scheduler flags are forced to false."
& $Py (Join-Path $Root "scripts\serve_lhb_product.py") --db $ResolvedDb --port $Port
exit $LASTEXITCODE
