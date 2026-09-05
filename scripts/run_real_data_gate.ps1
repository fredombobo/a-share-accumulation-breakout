<#
重跑真实数据门禁（闸门 D）—— 在当前 build 与当前生产库上生成一份新报告。

闸门 D 现在报三条：报告超过 24 小时、门禁报告不属于当前构建版本、不属于当前行情数据库。
这三条都是「证据过期」，不是能力缺失 —— 在当前身份上重跑一次就该消失。

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_real_data_gate.ps1

本脚本不改任何 feature flag，不写生产库（门禁只读采样核验）。
它替代了原先散落在 runtime\ 下、把路径写死到工作树的 run_real_gate*.ps1。
#>

param(
    [string]$Root   = $(Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$DbPath = '',
    [int]$Days      = 730
)

$ErrorActionPreference = 'Stop'
$__prevOut = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Say([string]$t, [string]$c = 'Gray') { Write-Host $t -ForegroundColor $c }

if (-not $DbPath) { $DbPath = Join-Path $Root 'runtime\stock_data.db' }
$envFile = Join-Path $Root '.env'
$python  = Join-Path $Root '.venv312\Scripts\python.exe'

foreach ($p in @($Root, $DbPath, $envFile, $python)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "路径不存在：$p" }
}
if ($DbPath -match 'lhb_product') { throw '拒绝：门禁应针对生产行情库，不是龙虎榜副本。' }

# 环境净化（与 daily_run.ps1 同一套规矩）
Remove-Item Env:PYTHONPATH -EA SilentlyContinue
foreach ($k in 'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy') {
    Remove-Item "Env:$k" -EA SilentlyContinue
}

# 只取这两个键，Token 不回显
$allowed = @('TUSHARE_TOKEN', 'TUSHARE_HTTP_URL')
$loaded = @()
foreach ($line in (Get-Content -LiteralPath $envFile)) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $parts = $t.Split('=', 2)
    $key = $parts[0].Trim()
    if ($key -in $allowed) {
        [Environment]::SetEnvironmentVariable($key, $parts[1].Trim().Trim('"').Trim("'"), 'Process')
        $loaded += $key
    }
}
if ($loaded -notcontains 'TUSHARE_TOKEN') { throw "$envFile 里没有 TUSHARE_TOKEN。" }

# 硬门只关不开
$env:LIVE_TRADING_ENABLED    = 'false'
$env:DAILY_SCHEDULER_ENABLED = 'false'
$env:V2_PIT_READ_ENABLED     = 'false'
$env:AB_DB_PATH = $DbPath

$stamp     = Get-Date -Format 'yyyyMMdd_HHmmss'
$reportDir = Join-Path $Root ("runtime\v2\real-data-gates\{0}" -f $stamp)
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null

Say ''
Say "真实数据门禁  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'White'
Say "  代码   $Root"
Say "  行情库 $DbPath"
Say "  回看   $Days 个交易日"
Say "  报告   $reportDir"
Say ''
Say '运行中（源端抽样核验，通常几分钟）…' 'Cyan'

Push-Location $Root
try {
    & $python -m paper_trading.real_data_gate --db $DbPath --days $Days --report $reportDir
    $code = $LASTEXITCODE
} finally { Pop-Location }

Say ''
if ($code -ne 0) {
    Say "门禁退出码 $code —— 未通过或执行失败。报告在 $reportDir" 'Yellow'
} else {
    Say '门禁执行完成。' 'Green'
}

$report = Get-ChildItem -LiteralPath $reportDir -Filter '*.json' -EA SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($report) {
    Say ''
    Say "报告：$($report.FullName)" 'White'
    try {
        $j = [System.IO.File]::ReadAllText($report.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        foreach ($k in 'status','verdict','trade_days','sampled_days','mismatches','code_version','db_fingerprint','report_sha256') {
            if ($null -ne $j.$k) { Say ("  {0,-16} {1}" -f $k, $j.$k) }
        }
    } catch {
        Say "  （报告解析失败，直接看文件）" 'DarkGray'
    }
}

Say ''
Say '接着重启后端让闸门重新评估：' 'White'
Say '    停止.bat  →  每日运行.bat  -SkipSync -SkipScan' 'White'
Say '然后看 readiness 里 D 是否已从 blocked_gates 消失。' 'DarkGray'
Say ''
Say 'D 变绿不代表研究通过 —— R（权威研究 FAIL）不受此影响。' 'DarkYellow'
Say ''

try { [Console]::OutputEncoding = $__prevOut } catch { }

# 必须显式传出退出码。
# 2026-09-03 教训：这里原本自然结束，子 PowerShell 永远返回 0，于是 daily_run.ps1
# 的第 6a 步在门禁 FAIL 的情况下照样打印「OK 闸门 D 报告已刷新」。
# 把 FAIL 显示成 OK 是这个项目里最不能出现的一类错误。
exit $code
