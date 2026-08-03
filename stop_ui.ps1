# 停止荐股 UI 进程
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root "runtime"

function Stop-PidFile([string]$name) {
  $f = Join-Path $LogDir $name
  if (Test-Path $f) {
    $id = (Get-Content $f -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($id -match '^\d+$') {
      Stop-Process -Id ([int]$id) -Force -ErrorAction SilentlyContinue
      Write-Host "stopped $name pid=$id"
    }
    Remove-Item $f -Force -ErrorAction SilentlyContinue
  }
}

Stop-PidFile "backend.pid"
Stop-PidFile "frontend.pid"

# 兜底：按端口杀
foreach ($port in 8000, 3001) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "freed port $port pid=$($_.OwningProcess)"
  }
}
Write-Host "done"
