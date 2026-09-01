<#
注册 / 移除「每交易日盘后自动跑一次」的 Windows 计划任务。

    注册（默认 周一~周五 18:30）：
        powershell -NoProfile -ExecutionPolicy Bypass -File setup_daily_task.ps1
    换时间：
        ... -File setup_daily_task.ps1 -At 19:00
    查看：
        ... -File setup_daily_task.ps1 -Show
    移除：
        ... -File setup_daily_task.ps1 -Remove

说明：
  · 这只是替你按时执行 daily_run.ps1，跑的是同一条手动链路。
  · 与应用内部的 DAILY_SCHEDULER_ENABLED 无关 —— 那个仍然是 false，本脚本不碰。
  · 只在你登录时运行，不需要保存密码。机器关着错过的那次，开机后会补跑。
  · 节假日照跑：同步按交易日历 diff，没有新交易日就是 0 行，扫描结果 as_of 不变，
    不会产生假信号，只会多一份日志。
#>

param(
    [string]$Root     = 'E:\CODEX\Stock_selection\accumulation_breakout',
    [string]$TaskName = 'AB-DailyScreener',
    [string]$At       = '18:30',
    [switch]$Remove,
    [switch]$Show
)

$ErrorActionPreference = 'Stop'
$__prevOut = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Say([string]$t, [string]$c = 'Gray') { Write-Host $t -ForegroundColor $c }

# ---------------------------------------------------------------- 查看
if ($Show) {
    $t = Get-ScheduledTask -TaskName $TaskName -EA SilentlyContinue
    if (-not $t) { Say "任务 $TaskName 不存在。" 'Yellow'; exit 0 }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Say ''
    Say "任务      : $TaskName" 'White'
    Say "状态      : $($t.State)"
    Say "触发      : $(($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ', ')"
    Say "上次运行  : $($info.LastRunTime)   结果=$($info.LastTaskResult)"
    Say "下次运行  : $($info.NextRunTime)"
    Say ''
    Say "最近日志：" 'White'
    Get-ChildItem (Join-Path $Root 'runtime') -Filter 'daily_task_*.log' -EA SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 5 |
        ForEach-Object { Say ("  {0}  {1:N0} KB" -f $_.Name, ($_.Length / 1KB)) }
    Say ''
    try { [Console]::OutputEncoding = $__prevOut } catch { }
    exit 0
}

# ---------------------------------------------------------------- 移除
if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -EA SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Say "已移除任务 $TaskName" 'Green'
    } else {
        Say "任务 $TaskName 本来就不存在。" 'Yellow'
    }
    try { [Console]::OutputEncoding = $__prevOut } catch { }
    exit 0
}

# ---------------------------------------------------------------- 注册
$wrapper = Join-Path $Root 'daily_task.ps1'
if (-not (Test-Path -LiteralPath $wrapper)) { throw "找不到包装器：$wrapper" }
if (-not (Test-Path -LiteralPath (Join-Path $Root 'daily_run.ps1'))) { throw "找不到 daily_run.ps1" }

try { $null = [datetime]::Parse($At) } catch { throw "时间格式不对：$At（应形如 18:30）" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Root "{1}"' -f $wrapper, $Root) `
    -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -EA SilentlyContinue) {
    Say "已存在同名任务，先移除再重建…" 'Yellow'
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description '横盘吸筹→启动：每交易日盘后同步行情并跑一次全市场扫描（研究用，非下单）' `
    -Action $action -Trigger $trigger -Settings $settings | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Say ''
Say "OK  已注册 $TaskName" 'Green'
Say "    周一~周五 $At 自动运行；下次 $($info.NextRunTime)"
Say "    日志写到 $Root\runtime\daily_task_*.log（只留最近 30 份）"
Say ''
Say '硬门未被改动：LIVE_TRADING / DAILY_SCHEDULER / V2_PIT_READ 仍由 daily_run.ps1 强制 false。' 'DarkGray'
Say ''
Say '立刻试跑一次（不用等到点）：' 'White'
Say "    Start-ScheduledTask -TaskName $TaskName" 'White'
Say '查看状态与日志：' 'White'
Say "    ... -File setup_daily_task.ps1 -Show" 'White'
Say ''

try { [Console]::OutputEncoding = $__prevOut } catch { }
