$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$LogDir = Join-Path $Root 'runtime'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LaunchLog = Join-Path $LogDir 'launch_desktop.log'

function Log([string]$msg) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $msg
  Add-Content -Path $LaunchLog -Value $line -Encoding UTF8
  Write-Host $line
}

try {
  try { $host.UI.RawUI.WindowTitle = 'AB-Screener starting...' } catch {}
  Write-Host ''
  Write-Host '  ========================================' -ForegroundColor Cyan
  Write-Host '    AB-Screener  Stock Screener UI' -ForegroundColor Cyan
  Write-Host '  ========================================' -ForegroundColor Cyan
  Write-Host ''
  Log ('begin root=' + $Root)

  $startScript = Join-Path $Root 'start_ui.ps1'
  if (-not (Test-Path -LiteralPath $startScript)) {
    throw ('missing start_ui.ps1: ' + $startScript)
  }

  & $startScript 2>&1 | ForEach-Object { Log ('  ' + $_); $_ }
  Log 'start_ui finished'

  $ready = $false
  for ($i = 0; $i -lt 50; $i++) {
    try {
      # 单端口模式：后端 8001 自带前端，无需等待 :3001 dev 服务
      $r = Invoke-WebRequest 'http://127.0.0.1:8001/api/health' -UseBasicParsing -TimeoutSec 2
      if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 400 }
  }

  Write-Host ''
  if ($ready) {
    Write-Host '  Ready. Opening browser...' -ForegroundColor Green
    Log 'backend ready'
  } else {
    Write-Host '  Backend still starting, open browser anyway...' -ForegroundColor Yellow
    Log 'backend not ready'
  }
  Start-Process 'http://127.0.0.1:8001/'

  Write-Host ''
  Write-Host ('  Log: ' + $LaunchLog) -ForegroundColor DarkGray
  Write-Host '  Stop: run stop_ui.ps1' -ForegroundColor DarkGray
  Write-Host '  Window closes in 8 seconds (services keep running)' -ForegroundColor DarkGray
  Start-Sleep -Seconds 8
}
catch {
  Log ('ERROR: ' + $_.Exception.Message)
  Write-Host ''
  Write-Host '  START FAILED' -ForegroundColor Red
  Write-Host ('  ' + $_.Exception.Message) -ForegroundColor Red
  Write-Host ('  See log: ' + $LaunchLog) -ForegroundColor Yellow
  $be = Join-Path $LogDir 'backend.err.log'
  $fe = Join-Path $LogDir 'frontend.err.log'
  if (Test-Path $be) { Write-Host '  backend.err:'; Get-Content $be -Tail 12 }
  if (Test-Path $fe) { Write-Host '  frontend.err:'; Get-Content $fe -Tail 12 }
  Write-Host ''
  Write-Host '  Press Enter to close...' -ForegroundColor Yellow
  try { [void](Read-Host) } catch { Start-Sleep 45 }
  exit 1
}
