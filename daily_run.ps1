<#
每日运行 —— 横盘吸筹→启动 选股系统的单一日常入口。

做四件事，按顺序 fail-closed：
    1) 净化环境（代理 / PYTHONPATH），只从 .env 读取 Token
    2) 增量同步行情到生产库（失败即停，不允许用过期数据出结果）
    3) 拉起 8001 单端口后端（绑定生产库，硬门强制 false）
    4) 触发一次全市场扫描，等待完成，打印 A/B 池与市场环境

本脚本永不打开 LIVE_TRADING_ENABLED / DAILY_SCHEDULER_ENABLED / V2_PIT_READ_ENABLED，
永不指向龙虎榜产品副本 lhb_product.db，永不写研究结论。
输出的 A 池是研究候选，不是荐股，也不是买入指令。

用法：
    双击  每日运行.bat
    或    powershell -NoProfile -ExecutionPolicy Bypass -File 每日运行.ps1
    只开界面不扫描：  -SkipScan
    行情已同步过：    -SkipSync
#>

param(
    # 2026-08-31 收口后：主副本已含全部合并结果，不再指向集成工作树。
    [string]$Root    = 'E:\CODEX\Stock_selection\accumulation_breakout',
    [string]$DbPath  = 'E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db',
    [string]$EnvFile = 'E:\CODEX\Stock_selection\accumulation_breakout\.env',
    [string]$Python  = 'E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe',
    [string]$BackupRoot = 'E:\ab-backups',
    [int]$Port       = 8001,
    [int]$Top        = 20,
    [int]$Days       = 160,
    [int]$ScanTimeoutMinutes = 30,
    [switch]$SkipSync,
    [switch]$SkipScan,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Step([int]$n, [string]$t) {
    Write-Host ''
    Write-Host ("[{0}/5] {1}" -f $n, $t) -ForegroundColor Cyan
    Write-Host ('-' * 60) -ForegroundColor DarkGray
}
function Ok([string]$t)   { Write-Host "  OK  $t" -ForegroundColor Green }
function Warn([string]$t) { Write-Host "  !   $t" -ForegroundColor Yellow }
function Die([string]$t)  { Write-Host ''; Write-Host "  X   $t" -ForegroundColor Red; Write-Host ''; exit 1 }

function Test-Port([int]$p) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $p); $true } catch { $false } finally { $c.Dispose() }
}

# Windows PowerShell 5.1 会把无 BOM 的 UTF-8 当 ANSI(GBK) 解，中文变乱码、JSON 直接解析失败。
# 以下三个函数一律显式按 UTF-8 解码，不依赖系统代码页。
function Read-JsonFile([string]$path) {
    [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
}
function Get-JsonUtf8([string]$uri, [int]$sec = 15) {
    $r = Invoke-WebRequest -Uri $uri -TimeoutSec $sec -UseBasicParsing
    [System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
}
function Post-JsonUtf8([string]$uri, $obj, [int]$sec = 30) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress))
    $r = Invoke-WebRequest -Uri $uri -Method Post -ContentType 'application/json; charset=utf-8' `
        -Body $bytes -TimeoutSec $sec -UseBasicParsing
    [System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
}

Write-Host ''
Write-Host '横盘吸筹 -> 启动   每日运行' -ForegroundColor White
Write-Host ("时间 {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Host ("代码 {0}" -f $Root)
Write-Host ("行情库 {0}" -f $DbPath)

# ---------------------------------------------------------------- 1 环境
Step 1 '净化环境并加载 Token'

foreach ($p in @($Root, $DbPath, $EnvFile, $Python)) {
    if (-not (Test-Path -LiteralPath $p)) { Die "路径不存在：$p" }
}
if ($DbPath -match 'lhb_product') { Die '拒绝执行：本脚本不得指向龙虎榜产品副本。' }

Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
foreach ($k in 'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy') {
    Remove-Item "Env:$k" -ErrorAction SilentlyContinue
}

$allowed = @('TUSHARE_TOKEN', 'TUSHARE_HTTP_URL')
$loaded = @()
foreach ($line in (Get-Content -LiteralPath $EnvFile)) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $parts = $t.Split('=', 2)
    $key = $parts[0].Trim()
    if ($key -in $allowed) {
        [Environment]::SetEnvironmentVariable($key, $parts[1].Trim().Trim('"').Trim("'"), 'Process')
        $loaded += $key
    }
}
if ($loaded -notcontains 'TUSHARE_TOKEN') { Die "$EnvFile 里没有 TUSHARE_TOKEN。" }

# 硬门：本脚本只关不开
$env:LIVE_TRADING_ENABLED    = 'false'
$env:DAILY_SCHEDULER_ENABLED = 'false'
$env:V2_PIT_READ_ENABLED     = 'false'
$env:AB_DB_PATH      = $DbPath
$env:AB_BACKEND_PORT = "$Port"
# 闸门 O 的备份检查要求这个变量存在（与 start_backend_authoritative.ps1 保持一致）
if ($BackupRoot) {
    if (-not (Test-Path -LiteralPath $BackupRoot)) { New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null }
    $env:AB_BACKUP_ROOT = $BackupRoot
}

$dbGb = (Get-Item -LiteralPath $DbPath).Length / 1GB
Ok ("已加载 {0}；硬门全部 false；行情库 {1:N2} GB" -f ($loaded -join ', '), $dbGb)

# ---------------------------------------------------------------- 2 同步
Step 2 '增量同步行情（按交易日历补洞）'

if ($SkipSync) {
    Warn '已按 -SkipSync 跳过。若行情过期，后面的扫描结果不可用于当日判断。'
} else {
    Push-Location $Root
    try {
        & $Python 'sync_daily.py'
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    if ($code -ne 0) {
        Die '同步未完整完成（存在失败交易日）。请检查 Token / 网络后重跑；不要用不完整的数据出结论。'
    }
    Ok '行情同步完成'
}

# ---------------------------------------------------------------- 3 后端
Step 3 "拉起单端口后端 :$Port"

if (Test-Port $Port) {
    Ok "端口 $Port 已在监听，复用现有后端"
    $owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($owner) {
        $op = Get-Process -Id $owner.OwningProcess -ErrorAction SilentlyContinue
        if ($op -and $op.Path -and $op.Path -ne $Python) {
            Warn "该进程不是本脚本的解释器：$($op.Path)"
            Warn "它的环境变量（AB_BACKUP_ROOT / AB_DB_PATH）未必与本次一致。要对齐就先跑 停止.bat 再重来。"
        }
    }
} else {
    $outLog = Join-Path $Root 'runtime\backend.out.log'
    $errLog = Join-Path $Root 'runtime\backend.err.log'
    $proc = Start-Process -FilePath $Python -ArgumentList 'web\backend_app.py' `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -PassThru -WindowStyle Hidden
    $proc.Id | Out-File -LiteralPath (Join-Path $Root 'runtime\backend.pid') -Encoding ascii
    Write-Host "  启动中 PID=$($proc.Id) ..." -NoNewline
    $up = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        Write-Host '.' -NoNewline
        if (Test-Port $Port) { $up = $true; break }
    }
    Write-Host ''
    if (-not $up) { Die "后端 60 秒内未起来，看 $errLog" }
    Ok "后端已启动 PID=$($proc.Id)"
}

try {
    $st = Get-JsonUtf8 "http://127.0.0.1:$Port/api/v2/platform/status" 10

    # 只看这三个明确表示"实盘开关"的字段。
    # 注意 hard_gates.LIVE_TRADING_DISABLED = true 表示"已禁用"，是安全状态，不能当成实盘开启。
    $liveOn = $false
    foreach ($v in @($st.live, $st.live_trading_enabled, $st.flags.LIVE_TRADING_ENABLED)) {
        if ($null -ne $v -and [bool]$v) { $liveOn = $true }
    }
    if ($liveOn) { Die '后端报告实盘开关为 true —— 立即停止并人工核查，本脚本不继续。' }

    Ok ("身份 product={0} port={1} build={2}" -f $st.product, $st.default_port, $st.build_version)
    Ok ("实盘 live=false · 调度={0} · PIT读={1} · 执行写={2}" -f `
        $st.flags.DAILY_SCHEDULER_ENABLED, $st.flags.V2_PIT_READ_ENABLED, $st.flags.V2_EXECUTION_WRITE_ENABLED)

    if ($st.readiness -and $st.readiness -ne 'READY') {
        $blocked = (@($st.readiness_detail.blocked_gates) -join ',')
        $idb     = (@($st.readiness_detail.identity_blockers) -join ',')
        Warn ("七闸门 readiness={0}  受阻闸门={1}" -f $st.readiness, $blocked)
        if ($idb) { Warn ("身份阻断={0} —— 闸门证据产生于更早的 build，需在当前构建上重跑证据。" -f $idb) }
        Warn '这是研究/发布门禁，不阻断日常扫描。'
    }
} catch {
    Warn "身份接口未响应（不阻断扫描）：$($_.Exception.Message)"
}

# ---------------------------------------------------------------- 4 扫描
Step 4 '全市场扫描'

$result = $null
if ($SkipScan) {
    Warn '已按 -SkipScan 跳过扫描，下面显示的是上一次的结果。'
    $latest = Get-ChildItem (Join-Path $Root 'runtime') -Filter 'scan_*.result.json' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) { $result = Read-JsonFile $latest.FullName }
} else {
    $body = @{ top = $Top; days = $Days; force = $false }
    try {
        $start = Post-JsonUtf8 "http://127.0.0.1:$Port/api/scan" $body 30
    } catch {
        Die "发起扫描失败：$($_.Exception.Message)（若提示 409，说明已有扫描在跑，等它跑完或在界面取消）"
    }
    $taskId = $start.task_id
    Ok "扫描已发起 task_id=$taskId （通常 5-15 分钟）"

    $deadline = (Get-Date).AddMinutes($ScanTimeoutMinutes)
    $lastStage = ''
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        try {
            $s = Get-JsonUtf8 "http://127.0.0.1:$Port/api/scan/status?task_id=$taskId" 15
        } catch { continue }
        $stage = "$($s.status) / $($s.stage)"
        if ($stage -ne $lastStage) { Write-Host "  $((Get-Date).ToString('HH:mm:ss'))  $stage"; $lastStage = $stage }
        if ($s.status -in @('done', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'error')) { break }
    }

    $rf = Join-Path $Root "runtime\scan_$taskId.result.json"
    if (Test-Path $rf) { $result = Read-JsonFile $rf }
    else { Warn "未找到结果文件 $rf（可能仍在运行或已失败），改用最近一次结果。"
           $latest = Get-ChildItem (Join-Path $Root 'runtime') -Filter 'scan_*.result.json' -ErrorAction SilentlyContinue |
               Sort-Object LastWriteTime -Descending | Select-Object -First 1
           if ($latest) { $result = Read-JsonFile $latest.FullName } }
}

# ---------------------------------------------------------------- 5 结果
Step 5 '结果'

if (-not $result) {
    Warn '没有可显示的扫描结果。'
} else {
    Write-Host ''
    Write-Host ("  数据基准日 as_of : {0}   新鲜度 {1}" -f $result.latest_date, $result.freshness.stale_label)
    Write-Host ("  市场环境         : {0}   允许新开仓 {1}   最大持仓位 {2}" -f `
        $result.regime.label, $result.regime.allow_new_entries, $result.regime.max_trade_slots)
    Write-Host ("  A 池（可交易研究候选）: {0}" -f $result.count_a) -ForegroundColor White
    Write-Host ("  B 池（观察，勿与 A 混排）: {0}" -f $result.count_b)

    if ($result.pool_report.a_tiers) {
        $tiers = ($result.pool_report.a_tiers.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ' '
        Write-Host ("  A 池分层           : {0}" -f $tiers)
    }

    if ($result.count_a -gt 0) {
        Write-Host ''
        # 结果文件里的 candidate_codes 是形态命中的混合清单（本次 38 个），
        # 与 A 池 7 / B 池 30 对不上，无法可靠切出 A 池那几只。
        # 宁可不显示，也不能在选股场景里报一份可能错的标的清单。
        Write-Host '  A 池具体标的请在界面查看（终端无法可靠还原 A/B 归属）：' -ForegroundColor White
        Write-Host ("    http://127.0.0.1:{0}/" -f $Port) -ForegroundColor Cyan
    } elseif ($result.regime.allow_new_entries -eq $false) {
        Write-Host ''
        Warn 'A 池为空且环境为防守 —— 这是风控设计，不是故障。今日不新开仓。'
    }

    if ($result.strategy_profile.source_kind -eq 'MANUAL_RESEARCH') {
        Write-Host ''
        Warn ("当前生效参数是手工研究参数 {0}，未经 IS/OOS、WF、基线与成本压力验证。" -f $result.strategy_profile.version)
    }
}

Write-Host ''
Write-Host '  ------------------------------------------------------------'
Write-Host '  A 池是研究候选，不是荐股，不是买入指令。' -ForegroundColor DarkYellow
Write-Host '  是否买入、买多少，由你自己判断并承担结果。' -ForegroundColor DarkYellow
Write-Host '  ------------------------------------------------------------'
Write-Host ''
Write-Host ("  界面： http://127.0.0.1:{0}/" -f $Port) -ForegroundColor Cyan
Write-Host '  停止： 双击 停止.bat'
Write-Host ''

if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/" }
