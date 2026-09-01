<#
在当前构建上重新生成 S / P / L / O / G 五道闸门的证据。

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\rebuild_gate_evidence.ps1

做两件事：
  1) 跑供应商 HTTPS/TLS 探针 —— 闸门 G 的 G-14 就卡在这一项
  2) 从生产库真实读数，生成五份带身份签名的门禁文件到 runtime\v2\gates

**它不会让任何一道闸门"变绿"。** build_gate_artifacts_v2 的结论全部来自数据库
和证据文件的实际内容，这个脚本只是把结论重新算一遍并绑上当前的 code_version。
如果 08-29 那批是 FAIL，数据没变的话这批还是 FAIL —— 那是如实记录，不是失败。

前置条件（不满足会被下面挡住，不要绕过）：
  · 工作树干净 —— 脏工作树出具的生产证据没有可追溯性
  · 当日日清已跑过 —— 否则 L 的 manifest / DAG / cycle 仍停在旧交易日

依赖的三份外部证据：
  · 传输证据   本脚本现跑
  · 恢复演练   restore_backup_v2.py 的报告（耗时约 20 分钟，需约 16 GB 空闲）
  · 审计锚点   日清自动生成，在 -AnchorDir 下
#>

param(
    [string]$Root   = $(Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$DbPath = '',
    [string]$BackupRoot     = 'E:\ab-backups',
    [string]$AnchorDir      = 'E:\ab-backups\audit-anchors',
    [string]$SigningKeyFile = 'E:\ab-backups\security\audit-signing.key',
    [string]$RestoreReport  = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$__prevOut = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Say([string]$t, [string]$c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Die([string]$t) { Write-Host ''; Say "  X  $t" 'Red'; Write-Host ''; exit 1 }
function Read-JsonFile([string]$p) {
    [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
}

if (-not $DbPath) { $DbPath = Join-Path $Root 'runtime\stock_data.db' }
$python  = Join-Path $Root '.venv312\Scripts\python.exe'
$envFile = Join-Path $Root '.env'

foreach ($p in @($Root, $DbPath, $python, $envFile)) {
    if (-not (Test-Path -LiteralPath $p)) { Die "路径不存在：$p" }
}
if ($DbPath -match 'lhb_product') { Die '拒绝：门禁证据应针对生产库，不是龙虎榜副本。' }

# ---------------------------------------------------------------- 前置：工作树
Push-Location $Root
try { $dirty = (& git status --porcelain) } finally { Pop-Location }
if ($dirty -and -not $Force) {
    Say ''
    Say '  工作树不干净，build_gate_artifacts_v2 会直接拒绝出证据：' 'Yellow'
    $dirty | ForEach-Object { Say "    $_" 'DarkGray' }
    Say ''
    Say '  先提交或 stash 再跑。确实要看一眼中间结果：加 -Force（生成的证据不可用于验收）。' 'Yellow'
    Say ''
    exit 1
}

# ---------------------------------------------------------------- 环境
Remove-Item Env:PYTHONPATH -EA SilentlyContinue
foreach ($k in 'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy') {
    Remove-Item "Env:$k" -EA SilentlyContinue
}
$env:LIVE_TRADING_ENABLED    = 'false'
$env:DAILY_SCHEDULER_ENABLED = 'false'
$env:V2_PIT_READ_ENABLED     = 'false'
$env:AB_DB_PATH     = $DbPath
$env:AB_BACKUP_ROOT = $BackupRoot

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

Say ''
Say "重建门禁证据  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'White'
Say "  代码   $Root"
Say "  行情库 $DbPath"
Say "  备份   $BackupRoot"
Say ''

# ---------------------------------------------------------------- 1 传输探针
Say '[1/2] 供应商 HTTPS/TLS 探针（闸门 G · G-14）' 'Cyan'
Say ('-' * 60) 'DarkGray'

$transportDir = Join-Path $Root 'runtime\v2\transport'
New-Item -ItemType Directory -Path $transportDir -Force | Out-Null
$transportReport = Join-Path $transportDir "transport_$stamp.json"

Push-Location $Root
try {
    & $python 'scripts\check_vendor_tls.py' --env-file $envFile --report $transportReport
    $tlsCode = $LASTEXITCODE
} finally { Pop-Location }

if ($tlsCode -eq 0) {
    Say '  OK  传输证据 PASS' 'Green'
} else {
    Say '  !   传输证据 FAIL —— G-14 仍然阻断。' 'Yellow'
    Say '      08-29 那次是 WinError 10054（对端主动断开），多半是供应商侧限流或网络路径问题，' 'DarkGray'
    Say '      不是本机配置错。换个时间重试往往就过了。' 'DarkGray'
}

# ---------------------------------------------------------------- 2 生成证据
Say ''
Say '[2/2] 生成 S / P / L / O / G 证据' 'Cyan'
Say ('-' * 60) 'DarkGray'

if (-not $RestoreReport) {
    $auto = Get-ChildItem (Join-Path $Root 'runtime\v2\restore') -Filter '*.json' -EA SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($auto) { $RestoreReport = $auto.FullName }
}
if ($RestoreReport -and (Test-Path -LiteralPath $RestoreReport)) {
    Say "  恢复演练报告：$RestoreReport" 'DarkGray'
} else {
    Say '  !   没有恢复演练报告 —— 闸门 O 的 O-08 会判 INSUFFICIENT。' 'Yellow'
    Say '      重跑一次（约 20 分钟，需约 16 GB 空闲盘）：' 'DarkGray'
    Say "        $python scripts\restore_backup_v2.py --help" 'DarkGray'
    $RestoreReport = ''
}

$gateArgs = @(
    'scripts\build_gate_artifacts_v2.py'
    '--db', $DbPath
    '--output', (Join-Path $Root 'runtime\v2\gates')
    '--transport-report', $transportReport
    '--backup-root', $BackupRoot
    '--anchor-dir', $AnchorDir
    '--signing-key-file', $SigningKeyFile
)
if ($RestoreReport) { $gateArgs += @('--restore-report', $RestoreReport) }

Push-Location $Root
try {
    $built = & $python @gateArgs
    $gateCode = $LASTEXITCODE
} finally { Pop-Location }

if ($gateCode -ne 0) {
    Say ''
    Say ($built -join [Environment]::NewLine) 'DarkGray'
    Die '证据生成失败（上面是原因）。'
}

# ---------------------------------------------------------------- 汇总
Say ''
Say '本次结论' 'White'
Say ('=' * 68) 'DarkGray'

$paths = $built -join "`n" | ConvertFrom-Json
foreach ($gate in 'S','P','L','O','G') {
    $file = $paths.$gate
    if (-not $file -or -not (Test-Path -LiteralPath $file)) { continue }
    $j = Read-JsonFile $file
    $color = switch ($j.status) { 'PASS' { 'Green' } 'INSUFFICIENT' { 'Yellow' } default { 'Red' } }
    Say ("  {0}  {1,-12}  {2}" -f $gate, $j.status, $j.summary) $color
    foreach ($c in $j.checks) {
        if ($c.status -ne 'PASS') {
            Say ("        {0,-22} {1}   {2}" -f $c.check_id, $c.status, $c.reason) 'DarkGray'
        }
    }
}

Say ('=' * 68) 'DarkGray'
Say ''
Say '重启后端让闸门重新读取这批证据：' 'White'
Say '    .\停止.bat  →  .\每日运行.bat -SkipSync -SkipScan -SkipEod' 'White'
Say ''
Say 'INSUFFICIENT 不是 bug —— 是"证据还不够"的如实记录。' 'DarkYellow'
Say 'S 的 300 个成熟 outcome 与 O-12 的 5 个 soak 日只能靠时间累积，不能靠重跑。' 'DarkYellow'
Say ''

try { [Console]::OutputEncoding = $__prevOut } catch { }
