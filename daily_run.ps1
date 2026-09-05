<#
个人研究日用入口：同步 → 验证 8001 身份 → 扫描 → 核对日期。
默认不执行纸面日清、soak 或七闸门；-InstitutionalMaintenance 才启用旧运维。
-Backup 显式创建独立可验证备份。研究结果不是荐股。
#>

param(
    # 2026-08-31 收口后：主副本已含全部合并结果，不再指向集成工作树。
    [string]$Root    = 'E:\CODEX\Stock_selection\accumulation_breakout',
    [string]$DbPath  = 'E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db',
    [string]$EnvFile = 'E:\CODEX\Stock_selection\accumulation_breakout\.env',
    [string]$Python  = 'E:\CODEX\Stock_selection\accumulation_breakout\.venv312\Scripts\python.exe',
    [string]$BackupRoot = 'E:\ab-backups',
    [string]$AnchorDir  = 'E:\ab-backups\audit-anchors',
    [string]$SigningKeyFile = 'E:\ab-backups\security\audit-signing.key',
    [int]$Port       = 8001,
    [int]$Top        = 20,
    [int]$Days       = 160,
    [int]$ScanTimeoutMinutes = 30,
    [switch]$SkipSync,
    [switch]$SkipScan,
    [switch]$Backup,
    [switch]$InstitutionalMaintenance,
    [switch]$SkipEod,
    [switch]$SkipGates,
    [switch]$NoBrowser,
    [switch]$AllowForeignBackend
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$optionalFailed = $false
if ($Port -ne 8001 -or $AllowForeignBackend) { Write-Error '仅允许 AB/8001，不能绕过身份检查'; exit 1 }

function Step([int]$n, [string]$t) {
    Write-Host ''
    Write-Host ("[{0}/7] {1}" -f $n, $t) -ForegroundColor Cyan
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

# Python 子进程的中文输出编码。
# 不设这个的话，python 在"输出被重定向到管道"时会按系统代码页(GBK)写 stdout，
# 而 PowerShell 这一侧按 UTF-8 读，日志里 sync_daily.py 的中文就是整片乱码。
# 计划任务的日志正是管道模式，2026-09-03 那份日志就踩了这个坑。
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8       = '1'

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
        & $Python -u 'sync_daily.py'
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

Push-Location $Root
try {
    & $Python -m ab_screener.operations.runtime_identity --root $Root --db $DbPath
    if ($LASTEXITCODE -ne 0) { Die '身份检查失败。源码更新后请先重启 8001；不会强杀运行中的任务。' }
} finally { Pop-Location }
$health = Get-JsonUtf8 "http://127.0.0.1:$Port/api/health"
if (-not $health.as_of -or $health.as_of -ne $health.freshness.expected_as_of) { Die '行情不是最新已完成交易日，请先完成同步。' }

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
    $s = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        try {
            $s = Get-JsonUtf8 "http://127.0.0.1:$Port/api/scan/status?task_id=$taskId" 15
        } catch { continue }
        $stage = "$($s.status) / $($s.stage)"
        if ($stage -ne $lastStage) { Write-Host "  $((Get-Date).ToString('HH:mm:ss'))  $stage"; $lastStage = $stage }
        if ($s.status -in @('done', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'error', 'cancelled', 'interrupted')) { break }
    }

    if (-not $s -or $s.status -notin @('done', 'SUCCEEDED')) { Die '扫描失败、取消或超时；不读取旧结果。' }
    $rf = Join-Path $Root "runtime\scan_$taskId.result.json"
    if (Test-Path $rf) { $result = Read-JsonFile $rf }
    else {
        Write-Host ''
        Warn "本次扫描 $taskId 的结果文件不存在：$rf"
        Warn "常见原因：8001 上的后端来自另一个代码副本，结果写进了它自己的 runtime。"
        Die "不显示任何结果。绝不拿上一次的旧池子冒充本次扫描。"
    }
}

# ---------------------------------------------------------------- 5 日清
Step 5 '生产纸面日清（DAG · 风险 · 对账 · 审计锚点 · 备份 · soak）'

# 这一步推进闸门 L / O-07 / P 与 soak 天数。它对生产库是**追加写**（运维表），
# 不改行情数据，不产生交易指令。run_eod_v2 自身有四道前置断言：
#   · 只允许日清"库内最新交易日"
#   · 该交易日必须已有 status=SUCCEEDED 的扫描
#   · 该扫描的 code_version 必须等于当前构建（防止拿旧构建的扫描充数）
#   · 工作树必须干净（脏工作树拒绝出具生产证据）
# 任何一条不满足它自己会 FAIL 退出，这里只负责如实转述，不绕过。
$SoakDir = Join-Path $Root 'runtime\v2\soak'

if (-not $InstitutionalMaintenance) {
    Ok '个人研究模式：不运行纸面日清，不写账本或 soak。'
} elseif ($SkipEod) {
    Warn '已按 -SkipEod 跳过。闸门 L / O / P 与 soak 天数今天不会推进。'
} elseif ($SkipScan) {
    Warn '已跳过扫描，而日清要求"当前构建在当日的成功扫描"，因此一并跳过。'
} else {
    foreach ($d in @($AnchorDir, (Split-Path -Parent $SigningKeyFile), $SoakDir)) {
        if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }

    if (-not (Test-Path -LiteralPath $SigningKeyFile)) {
        # 审计签名密钥是信任根：一旦换掉，此前所有外部锚点都无法再验证。
        # 所以绝不由日常脚本自动生成，必须人工执行一次带 --initialize-signing-key 的命令。
        $optionalFailed = $true
        Warn "审计签名密钥不存在：$SigningKeyFile"
        Warn '不自动创建（换密钥会让历史锚点全部失效）。需要时手动执行一次：'
        Write-Host ("    {0} scripts\run_eod_v2.py --db `"{1}`" --anchor-dir `"{2}`" ``" -f $Python, $DbPath, $AnchorDir) -ForegroundColor White
        Write-Host ("      --signing-key-file `"{0}`" --soak-dir `"{1}`" --backup-root `"{2}`" ``" -f $SigningKeyFile, $SoakDir, $BackupRoot) -ForegroundColor White
        Write-Host '      --initialize-signing-key' -ForegroundColor White
    } else {
        $eodDir = Join-Path $Root 'runtime\v2\eod'
        if (-not (Test-Path -LiteralPath $eodDir)) { New-Item -ItemType Directory -Path $eodDir -Force | Out-Null }
        $eodReport = Join-Path $eodDir ("eod_{0}.json" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

        Write-Host '  运行中 —— 含整库压缩备份，通常 20-30 分钟…' -ForegroundColor Cyan
        Push-Location $Root
        try {
            $eodOut = & $Python 'scripts\run_eod_v2.py' `
                --db $DbPath --anchor-dir $AnchorDir --signing-key-file $SigningKeyFile `
                --soak-dir $SoakDir --backup-root $BackupRoot --report $eodReport 2>&1
            $eodCode = $LASTEXITCODE
        } finally { Pop-Location }

        if ($eodCode -ne 0) {
            $optionalFailed = $true
            Write-Host ''
            Write-Host '  日清未通过 —— A 池仍然可用，但闸门 L / O / P 与 soak 今天不前进。' -ForegroundColor Red
            Write-Host ($eodOut -join [Environment]::NewLine) -ForegroundColor DarkGray
            Write-Host ''
        } elseif (Test-Path -LiteralPath $eodReport) {
            $eod = Read-JsonFile $eodReport
            Ok ("日清完成 trade_date={0}  DAG={1}" -f $eod.trade_date, $eod.dag.status)
            Ok ("审计链 events={0} 有效={1}  外部锚点={2}" -f `
                $eod.audit.events, $eod.audit.chain_valid, $eod.audit.anchor_valid)
            if ($eod.backup) {
                Ok ("备份 {0:N2} GB  {1}" -f ($eod.backup.size_bytes / 1GB), (Split-Path -Leaf $eod.backup.path))
            }
            $soakDays = @($eod.soak.completed_trade_days).Count
            if ($eod.soak.status -eq 'PASS') {
                Ok ("soak {0}/{1} —— O-12 已满足" -f $soakDays, $eod.soak.required)
            } else {
                Warn ("soak {0}/{1} —— 还需 {2} 个完成交易日（不伪造，只能等）" -f `
                    $soakDays, $eod.soak.required, ($eod.soak.required - $soakDays))
            }
        } else {
            $optionalFailed = $true
        Warn "日清退出码 0 但没有报告文件：$eodReport"
        }
    }
}

# ---------------------------------------------------------------- 6 门禁证据
Step 6 '刷新门禁证据（D · S/P/L/O/G）'

# 为什么每天都要跑：闸门 D 的报告 24 小时过期，G 的传输探针只能证明"此刻"能连通，
# P/L/O 读的是当天日清写下的运维记录。这三类都是状态量，不是一次性成就。
# 两个子脚本都自带 fail-closed，这里只负责调用与如实转述，不解释、不掩盖。
if (-not $InstitutionalMaintenance) {
    Ok '个人研究模式：不执行机构七闸门。'
} elseif ($SkipGates) {
    Warn '已按 -SkipGates 跳过。闸门 D 的报告超过 24 小时后会自动失效。'
} elseif ($SkipEod -or $SkipScan -or $optionalFailed) {
    # 没跑日清就刷新，P/L 会拿旧交易日的 manifest 去签当前身份 —— 那是给过期结论
    # 盖新章，比不刷新更糟。宁可留着旧证据，也不产生误导性的新证据。
    Warn '本次未跑扫描/日清，刷新会把旧运维记录签成当前身份的新证据 —— 跳过。'
} else {
    $gateD = Join-Path $Root 'scripts\run_real_data_gate.ps1'
    $gateE = Join-Path $Root 'scripts\rebuild_gate_evidence.ps1'

    if (Test-Path -LiteralPath $gateD) {
        Write-Host '  [6a] 真实数据门禁 D —— 源端抽样核验，通常几分钟…' -ForegroundColor Cyan
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $gateD -Root $Root -DbPath $DbPath
        if ($LASTEXITCODE -ne 0) { $optionalFailed = $true; Warn '闸门 D 未通过（上面是报告内容）。' } else { Ok '闸门 D 报告已刷新' }
    } else { $optionalFailed = $true; Warn "找不到 $gateD" }

    if (Test-Path -LiteralPath $gateE) {
        Write-Host ''
        Write-Host '  [6b] 重建 S / P / L / O / G 证据…' -ForegroundColor Cyan
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $gateE `
            -Root $Root -DbPath $DbPath -BackupRoot $BackupRoot `
            -AnchorDir $AnchorDir -SigningKeyFile $SigningKeyFile
        if ($LASTEXITCODE -ne 0) { $optionalFailed = $true; Warn '证据重建未完成（多半是工作树不干净）。' }
    } else { $optionalFailed = $true; Warn "找不到 $gateE" }

    Write-Host ''
    Warn '证据刷新只更新"当前是什么状态"，不改变结论。G 断线、S 未成熟、R FAIL 都不会因此变绿。'
}

# ---------------------------------------------------------------- 7 结果
Step 7 '结果'

$dbLatest = $null
try {
    $dbLatest = (& $Python -c "import sqlite3,os;c=sqlite3.connect('file:'+os.environ['AB_DB_PATH']+'?mode=ro',uri=True);print(c.execute('SELECT MAX(trade_date) FROM daily').fetchone()[0] or '')" 2>$null).Trim()
} catch { }

if (-not $result) {
    Warn '没有可显示的扫描结果。'
} else {
    if ($dbLatest -and $result.latest_date -and "$($result.latest_date)" -ne "$dbLatest") {
        Write-Host ''
        Write-Host '  ############################################################' -ForegroundColor Red
        Write-Host ("  # 结果已过期：本次显示的是 {0} 的扫描，库内行情已到 {1}" -f $result.latest_date, $dbLatest) -ForegroundColor Red
        Write-Host '  # 不要按这份 A 池做任何判断。' -ForegroundColor Red
        Write-Host '  ############################################################' -ForegroundColor Red
    }
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

if (-not $SkipScan) {
    Push-Location $Root
    try {
        & $Python -m ab_screener.operations.personal_daily --db $DbPath --scan-result $rf
        if ($LASTEXITCODE -ne 0) { Die '日用记录验收失败，不标记完成。' }
    } finally { Pop-Location }
}
if ($Backup) {
    Push-Location $Root
    try {
        & $Python -m ab_screener.operations.personal_daily --db $DbPath --backup-root $BackupRoot
        if ($LASTEXITCODE -ne 0) { Die '独立备份失败。' }
    } finally { Pop-Location }
}
if ($optionalFailed) { Die '本次请求的机构运维步骤失败。' }
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/" }
exit 0
