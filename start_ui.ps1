# AB-Screener one-click start (backend :8000 + frontend :3001)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path 'C:\Python314\python.exe') { $Py = 'C:\Python314\python.exe' } else { $Py = 'python' }
$Backend = Join-Path $Root 'web\backend_app.py'
$Frontend = Join-Path $Root 'web\frontend'
$LogDir = Join-Path $Root 'runtime'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$NpmCmd = $null
foreach ($c in @('E:\Program Files\nodejs\npm.cmd','C:\Program Files\nodejs\npm.cmd', (Join-Path $env:ProgramFiles 'nodejs\npm.cmd'))) {
  if ($c -and (Test-Path -LiteralPath $c)) { $NpmCmd = $c; break }
}
if (-not $NpmCmd) {
  $n = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if ($n) { $NpmCmd = $n.Source }
}

function Test-Port([int]$Port) {
  try {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $c)
  } catch {
    $pat = ':' + $Port + '.*LISTENING'
    $out = netstat -ano 2>$null | Select-String $pat
    return ($null -ne $out)
  }
}

Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
foreach ($k in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')) {
  Remove-Item ('Env:' + $k) -ErrorAction SilentlyContinue
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

if (Test-Port 8000) {
  Write-Host '[backend] already on :8000' -ForegroundColor Yellow
} else {
  Write-Host '[backend] starting :8000 ...'
  $blogOut = Join-Path $LogDir 'backend.out.log'
  $blogErr = Join-Path $LogDir 'backend.err.log'
  $bpid = Join-Path $LogDir 'backend.pid'
  $p = Start-Process -FilePath $Py -ArgumentList $Backend -WorkingDirectory (Join-Path $Root 'web') -RedirectStandardOutput $blogOut -RedirectStandardError $blogErr -PassThru -WindowStyle Hidden
  Set-Content -Path $bpid -Value $p.Id -Encoding ascii
  Write-Host ('[backend] pid=' + $p.Id)
}

if (Test-Port 3001) {
  Write-Host '[frontend] already on :3001' -ForegroundColor Yellow
} else {
  if (-not $NpmCmd) { throw 'npm.cmd not found. Install Node.js first.' }
  $nm = Join-Path $Frontend 'node_modules'
  if (-not (Test-Path -LiteralPath $nm)) {
    Write-Host '[frontend] npm install ...'
    Push-Location $Frontend
    & $NpmCmd install
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { throw 'npm install failed' }
  }
  Write-Host '[frontend] starting :3001 ...'
  $flogOut = Join-Path $LogDir 'frontend.out.log'
  $flogErr = Join-Path $LogDir 'frontend.err.log'
  $fpid = Join-Path $LogDir 'frontend.pid'
  $argList = '/c ""' + $NpmCmd + '" run dev -- --host 127.0.0.1 --port 3001"'
  $p2 = Start-Process -FilePath 'cmd.exe' -ArgumentList $argList -WorkingDirectory $Frontend -RedirectStandardOutput $flogOut -RedirectStandardError $flogErr -PassThru -WindowStyle Hidden
  Set-Content -Path $fpid -Value $p2.Id -Encoding ascii
  Write-Host ('[frontend] pid=' + $p2.Id)
}

$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    $h = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
    if ($h.status -eq 'ok') { $ok = $true; break }
  } catch {
    Start-Sleep -Milliseconds 500
  }
}
if ($ok) {
  Write-Host '[health] ok' -ForegroundColor Green
} else {
  Write-Host '[health] backend not ready - see runtime\backend.err.log' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'UI:  http://127.0.0.1:3001/' -ForegroundColor Green
Write-Host 'API: http://127.0.0.1:8000/api/health'
Write-Host 'Stop: .\stop_ui.ps1'
