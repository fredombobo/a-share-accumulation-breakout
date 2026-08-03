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

# 兜底1：按端口杀
foreach ($port in 8000, 3001) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "freed port $port pid=$($_.OwningProcess)"
  }
}

# 兜底2（关键）：按命令行匹配杀全部相关进程（含扫描 worker 孤儿、泄漏的 node 子进程）
#  - 后端 python/pythonw：命令行含 backend_app.py 或项目路径
#  - 扫描 worker 孤儿：multiprocessing spawn 的命令行含 --multiprocessing-fork / spawn_main
#    （父进程被强杀后 worker 会永久挂机等待队列，必须清理）
#  - 前端 node/npm/cmd：命令行含 frontend / vite / npm run dev
$targets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  ($_.Name -match '^(python|pythonw)\.exe$' -and $_.CommandLine -match 'backend_app\.py|accumulation_breakout') -or
  ($_.Name -match '^(python|pythonw)\.exe$' -and $_.CommandLine -match 'multiprocessing-fork|spawn_main') -or
  ($_.Name -match '^(node|cmd)\.exe$' -and $_.CommandLine -match 'frontend|vite|npm run dev')
}
foreach ($t in $targets) {
  # 整棵进程树一起杀（node 的 cmd 包装器 → node 子进程）
  & taskkill /PID $t.ProcessId /T /F 2>$null | Out-Null
  Write-Host "killed tree $($t.Name) pid=$($t.ProcessId)"
}

Write-Host "done"
