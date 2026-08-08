from pathlib import Path

# 根路径从脚本自身位置推导，项目迁移后仍可用
root = Path(__file__).resolve().parent

start_ui = r"""# AB-Screener one-click start (backend :8000 + frontend :3001)
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
"""

launch = r"""$ErrorActionPreference = 'Continue'
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
      $r = Invoke-WebRequest 'http://127.0.0.1:3001/' -UseBasicParsing -TimeoutSec 2
      if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 400 }
  }

  Write-Host ''
  if ($ready) {
    Write-Host '  Ready. Opening browser...' -ForegroundColor Green
    Log 'frontend ready'
  } else {
    Write-Host '  Frontend still starting, open browser anyway...' -ForegroundColor Yellow
    Log 'frontend not ready'
  }
  Start-Process 'http://127.0.0.1:3001/'

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
"""

vbs = (
    'Set sh = CreateObject("WScript.Shell")\r\n'
    'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
    'root = fso.GetParentFolderName(WScript.ScriptFullName)\r\n'
    'ps1 = root & "\\launch_desktop.ps1"\r\n'
    'cmd = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """"\r\n'
    'sh.Run cmd, 1, False\r\n'
)

(root / "start_ui.ps1").write_text(start_ui, encoding="utf-8-sig", newline="\r\n")
(root / "launch_desktop.ps1").write_text(launch, encoding="utf-8-sig", newline="\r\n")
(root / "launch_desktop.vbs").write_text(vbs, encoding="ascii", newline="\r\n")
print("written OK")
