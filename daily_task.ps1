<#
计划任务包装器 —— 供 Windows 计划任务调用，不建议手动跑。

它做三件事：
  1) 以子进程方式调用 daily_run.ps1（拿得到真实退出码，计划任务的重试才有依据）
  2) 全部输出以 UTF-8 落到 runtime\daily_task_<时间戳>.log
     （不能用 Tee-Object：PowerShell 5.1 的 Tee 没有 -Encoding，默认写 UTF-16，
       追加进 UTF-8 文件会整片乱码）
  3) 只保留最近 30 份日志

退出码非 0 是有意义的：行情未发布、同步不完整都会走到这里，
计划任务据此触发重试。手动跑请直接双击 每日运行.bat（那个会开浏览器）。
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

# UTF-8 无 BOM，一个句柄写到底，编码不会中途变
$utf8 = New-Object System.Text.UTF8Encoding($false)
$sw = New-Object System.IO.StreamWriter($log, $true, $utf8)
$code = 2
try {
    $sw.WriteLine("=== 计划任务启动 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===")
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $daily -NoBrowser 2>&1 |
        ForEach-Object {
            $line = $_.ToString()
            Write-Host $line
            $sw.WriteLine($line)
        }
    $code = $LASTEXITCODE
    $sw.WriteLine("=== 结束 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  退出码=$code ===")
} finally {
    $sw.Flush(); $sw.Close()
}

# 日志轮转：只留最近 N 份
Get-ChildItem -LiteralPath $logDir -Filter 'daily_task_*.log' -EA SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $KeepLogs |
    Remove-Item -Force -EA SilentlyContinue

try { [Console]::OutputEncoding = $__prevOut } catch { }
exit $code
