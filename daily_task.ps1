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

# AutoFlush 必须开。
# 2026-09-02 教训：原来只在 finally 里 Flush，结果那天的任务被中途终止
# （LastTaskResult=267014），finally 没跑到，日志是 0 KB —— 恰恰在最需要日志的
# 时候什么都没留下。诊断日志必须逐行落盘，性能代价可以忽略。
$sw.AutoFlush = $true

$code = 2
$started = Get-Date
try {
    $sw.WriteLine("=== 计划任务启动 $($started.ToString('yyyy-MM-dd HH:mm:ss')) ===")
    $sw.WriteLine("    包装器 PID=$PID   目标=$daily")
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $daily -NoBrowser 2>&1 |
        ForEach-Object {
            $line = $_.ToString()
            Write-Host $line
            $sw.WriteLine("$((Get-Date).ToString('HH:mm:ss'))  $line")
        }
    $code = $LASTEXITCODE
    $mins = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
    $sw.WriteLine("=== 结束 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  退出码=$code  耗时 $mins 分钟 ===")
    $sw.WriteLine('    注：8001 后端仍在运行（有意为之，方便随后打开界面）。')
    $sw.WriteLine('    如果这一行之后计划任务仍显示 Running，说明它在等后端退出，')
    $sw.WriteLine('    会在 ExecutionTimeLimit 到点时被判 267014（被终止）—— 那不代表本次跑失败。')
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
