<#
计划任务包装器 —— 供 Windows 计划任务调用，不建议手动跑。

它做三件事：
  1) 以子进程方式调用 daily_run.ps1（拿得到真实退出码）
  2) 全部输出落到 runtime\daily_task_<时间戳>.log
  3) 只保留最近 30 份日志

手动跑请直接双击 每日运行.bat —— 那个会开浏览器，这个不会。
#>

param(
    [string]$Root = 'E:\CODEX\Stock_selection\accumulation_breakout',
    [int]$KeepLogs = 30
)

$ErrorActionPreference = 'Continue'
$__prevOut = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$daily = Join-Path $Root 'daily_run.ps1'
$logDir = Join-Path $Root 'runtime'
if (-not (Test-Path -LiteralPath $daily))  { Write-Error "找不到 $daily"; exit 2 }
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

$log = Join-Path $logDir ("daily_task_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

"=== 计划任务启动 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -LiteralPath $log -Encoding utf8

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $daily -NoBrowser 2>&1 |
    Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE

"=== 结束 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  退出码=$code ===" |
    Out-File -LiteralPath $log -Append -Encoding utf8

# 日志轮转：只留最近 N 份
Get-ChildItem -LiteralPath $logDir -Filter 'daily_task_*.log' -EA SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $KeepLogs |
    Remove-Item -Force -EA SilentlyContinue

try { [Console]::OutputEncoding = $__prevOut } catch { }
exit $code
