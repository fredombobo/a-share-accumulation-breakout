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
  · 失败（含行情尚未发布）会隔 45 分钟自动重试，最多 3 次。
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
    $rc = $info.LastTaskResult
    # 这几个是 Task Scheduler 的状态码，不是脚本退出码。裸数字没人看得懂，直接翻译。
    $rcText = switch ($rc) {
        0      { '成功' }
        1      { '脚本以 1 退出 —— 多半是行情未发布，等重试' }
        267009 { '正在运行' }
        267011 { '从未运行过（重新注册后会重置历史）' }
        267014 { '被终止 —— 见下方说明，不一定是失败' }
        default { '' }
    }
    $rcColor = if ($rc -eq 0) { 'Green' } elseif ($rc -eq 267011 -or $rc -eq 267009) { 'Gray' } else { 'Yellow' }

    Say ''
    Say "任务      : $TaskName" 'White'
    Say "状态      : $($t.State)"
    Say "触发      : $(($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ', ')"
    Say "上次运行  : $($info.LastRunTime)"
    Say ("结果      : {0}  {1}" -f $rc, $rcText) $rcColor
    Say "下次运行  : $($info.NextRunTime)"
    Say ''
    Say "最近日志：" 'White'
    $logs = @(Get-ChildItem (Join-Path $Root 'runtime') -Filter 'daily_task_*.log' -EA SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 5)
    foreach ($f in $logs) {
        $kb = $f.Length / 1KB
        $c  = if ($f.Length -eq 0) { 'Yellow' } else { 'Gray' }
        Say ("  {0}  {1:N0} KB{2}" -f $f.Name, $kb, $(if ($f.Length -eq 0) { '   ← 空日志，那次没留下任何记录' } else { '' })) $c
    }

    if ($rc -eq 267014) {
        Say ''
        Say '关于 267014（被终止）：' 'White'
        Say '  daily_run.ps1 会拉起 8001 后端并让它继续运行（有意为之）。计划任务把它算作' 'DarkGray'
        Say '  自己的子进程，于是任务一直显示 Running，直到 ExecutionTimeLimit（2 小时）到点被' 'DarkGray'
        Say '  强制终止 —— 这时 LastTaskResult 就是 267014。' 'DarkGray'
        Say '  判断当天到底跑没跑成，不要看这个码，看这三样：' 'DarkGray'
        Say '    runtime\v2\eod\eod_<当天>.json     日清报告存在即闭环' 'White'
        Say '    runtime\v2\soak\<当天>.json        soak 证据当天已收集' 'White'
        Say '    E:\ab-backups 下当天的 .db.gz     备份已生成' 'White'
    }
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

# 失败自动重试：Tushare 的 daily_basic / moneyflow 发布时间会飘，
# 18:30 撞上晚发布时 sync 会 fail-closed 退出 1。让计划任务隔 45 分钟再试，
# 最多 3 次（约 19:15 / 20:00 / 20:45），比把时间一味推后更稳。
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -RestartInterval (New-TimeSpan -Minutes 45) `
    -RestartCount 3

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
Say "    失败自动重试：隔 45 分钟一次，最多 3 次（行情晚发布不至于白等一天）"
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
