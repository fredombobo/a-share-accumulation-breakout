# AB-Screener one-click start (backend :8001 + frontend :3001)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendPort = 8001
$FrontendPort = 3001
$BackendUrl = "http://127.0.0.1:$BackendPort"
$LocalRuntime = Join-Path $Root '.venv312\Scripts\python.exe'
if (Test-Path -LiteralPath $LocalRuntime) { $LauncherPython = $LocalRuntime } else { $LauncherPython = 'python' }
$RuntimeResult = @(& $LauncherPython (Join-Path $Root 'launcher_runtime.py'))
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 setup failed; see the error above.' }
$Py = [string]$RuntimeResult[-1]
$Backend = Join-Path $Root 'web\backend_app.py'
$Frontend = Join-Path $Root 'web\frontend'
$LogDir = Join-Path $Root 'runtime'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$NpmCmd = $null
foreach ($candidate in @('E:\Program Files\nodejs\npm.cmd','C:\Program Files\nodejs\npm.cmd', (Join-Path $env:ProgramFiles 'nodejs\npm.cmd'))) {
  if ($candidate -and (Test-Path -LiteralPath $candidate)) { $NpmCmd = $candidate; break }
}
if (-not $NpmCmd) {
  $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if ($npm) { $NpmCmd = $npm.Source }
}

function Test-Port([int]$Port) {
  try {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $connection)
  } catch {
    $pattern = ':' + $Port + '.*LISTENING'
    $output = netstat -ano 2>$null | Select-String $pattern
    return ($null -ne $output)
  }
}

function Get-AbHealth {
  try {
    $health = Invoke-RestMethod "$BackendUrl/api/health" -TimeoutSec 2
    if ($null -eq $health -or $health.status -ne 'ok') { return $null }
    $names = @($health.PSObject.Properties.Name)
    if ($names -contains 'scanner_engine') { return $health }
    if (($names -contains 'build_version') -and $health.build_version) { return $health }
    if ($names -contains 'guided_ui_enabled') { return $health }
    return $null
  } catch {
    return $null
  }
}

Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
foreach ($key in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')) {
  Remove-Item ('Env:' + $key) -ErrorAction SilentlyContinue
}

if ($NpmCmd) {
  $nodeDir = Split-Path $NpmCmd -Parent
  if ($env:Path -notlike ('*' + $nodeDir + '*')) {
    $env:Path = $nodeDir + ';' + $env:Path
  }
}

Write-Host '=== AB-Screener UI ===' -ForegroundColor Cyan
Write-Host ('Root: ' + $Root)
Write-Host ('Python: ' + $Py)
Write-Host ('npm: ' + $NpmCmd)

$abHealth = Get-AbHealth
if ($abHealth) {
  Write-Host "[backend] AB Screener already on :$BackendPort" -ForegroundColor Yellow
} else {
  if (Test-Port $BackendPort) {
    throw "Port $BackendPort is occupied by another service. AB Screener will not stop or replace it."
  }
  Write-Host "[backend] starting :$BackendPort ..."
  $backendLogOut = Join-Path $LogDir 'backend.out.log'
  $backendLogErr = Join-Path $LogDir 'backend.err.log'
  $backendPid = Join-Path $LogDir 'backend.pid'
  $env:AB_BACKEND_PORT = [string]$BackendPort
  $process = Start-Process -FilePath $Py -ArgumentList $Backend -WorkingDirectory (Join-Path $Root 'web') -RedirectStandardOutput $backendLogOut -RedirectStandardError $backendLogErr -PassThru -WindowStyle Hidden
  Set-Content -Path $backendPid -Value $process.Id -Encoding ascii
  Write-Host ('[backend] pid=' + $process.Id)
}

if (Test-Port $FrontendPort) {
  Write-Host "[frontend] already on :$FrontendPort" -ForegroundColor Yellow
} else {
  if (-not $NpmCmd) { throw 'npm.cmd not found. Install Node.js first.' }
  $nodeModules = Join-Path $Frontend 'node_modules'
  if (-not (Test-Path -LiteralPath $nodeModules)) {
    Write-Host '[frontend] npm install ...'
    Push-Location $Frontend
    & $NpmCmd install
    $exitCode = $LASTEXITCODE
    Pop-Location
    if ($exitCode -ne 0) { throw 'npm install failed' }
  }
  Write-Host "[frontend] starting :$FrontendPort ..."
  $frontendLogOut = Join-Path $LogDir 'frontend.out.log'
  $frontendLogErr = Join-Path $LogDir 'frontend.err.log'
  $frontendPid = Join-Path $LogDir 'frontend.pid'
  $env:AB_BACKEND_URL = $BackendUrl
  $arguments = '/c ""' + $NpmCmd + '" run dev -- --host 127.0.0.1 --port ' + $FrontendPort + '"'
  $frontendProcess = Start-Process -FilePath 'cmd.exe' -ArgumentList $arguments -WorkingDirectory $Frontend -RedirectStandardOutput $frontendLogOut -RedirectStandardError $frontendLogErr -PassThru -WindowStyle Hidden
  Set-Content -Path $frontendPid -Value $frontendProcess.Id -Encoding ascii
  Write-Host ('[frontend] pid=' + $frontendProcess.Id)
}

$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
  if ($null -ne (Get-AbHealth)) {
    $ready = $true
    break
  }
  Start-Sleep -Milliseconds 500
}

if ($ready) {
  Write-Host '[health] AB Screener ok' -ForegroundColor Green
  try {
    $overview = Invoke-WebRequest "$BackendUrl/api/overview?pool=A" -UseBasicParsing -TimeoutSec 8
    if ($overview.StatusCode -eq 200) {
      Write-Host '[overview] ok' -ForegroundColor Green
    } else {
      Write-Host ('[overview] unexpected status ' + $overview.StatusCode) -ForegroundColor Yellow
    }
  } catch {
    Write-Host ('[overview] FAIL: ' + $_.Exception.Message) -ForegroundColor Red
  }
} else {
  Write-Host '[health] backend not ready - see runtime\backend.err.log' -ForegroundColor Red
}

Write-Host ''
Write-Host "UI:  http://127.0.0.1:$FrontendPort/" -ForegroundColor Green
Write-Host "API: $BackendUrl/api/health"
Write-Host 'AETF Alpha remains isolated on http://127.0.0.1:8000/'
Write-Host 'Stop: .\stop_ui.ps1'
