<#
收口诊断 —— 只读体检，不修改任何文件、不切分支、不合并、不推送。

用途：在正式收口（合并 v2r-final-integration 回 main）之前，
      把仓库真实状态、分支落差、冲突预览、运行态和数据绑定一次性打印出来。

用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File "E:\CODEX\Stock_selection\accumulation_breakout\收口诊断.ps1"

输出：控制台 + 报告文件 runtime\收口诊断-<时间戳>.txt
#>

param(
    [string]$Main = 'E:\CODEX\Stock_selection\accumulation_breakout',
    [string]$Work = 'E:\CODEX\Stock_selection\worktrees\v2r-final-integration',
    [switch]$NoFetch
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$reportDir = Join-Path $Main 'runtime'
if (-not (Test-Path $reportDir)) { New-Item -ItemType Directory -Path $reportDir -Force | Out-Null }
$report = Join-Path $reportDir "收口诊断-$stamp.txt"

# Windows PowerShell 5.1 默认按 ANSI(GBK) 解码子进程输出和无 BOM 的 UTF-8 文件，
# 会让 git 中文提交信息变乱码、让 ConvertFrom-Json 直接崩。这里统一按 UTF-8 处理。
$__prevOut = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Read-JsonFile([string]$path) {
    [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
}
function Get-JsonUtf8([string]$uri, [int]$sec = 15) {
    $r = Invoke-WebRequest -Uri $uri -TimeoutSec $sec -UseBasicParsing
    [System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
}

$lines = New-Object System.Collections.Generic.List[string]
function Emit([string]$s) { Write-Host $s; $lines.Add($s) | Out-Null }
function Section([string]$s) { Emit ''; Emit ('=' * 72); Emit $s; Emit ('=' * 72) }
function Run([string]$label, [scriptblock]$block) {
    Emit ''
    Emit "--- $label"
    try {
        $out = & $block 2>&1 | Out-String
        foreach ($l in ($out -split "`r?`n")) { if ($l.Trim()) { Emit "    $l" } }
    } catch {
        Emit "    [失败] $($_.Exception.Message)"
    }
}

Emit "收口诊断  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Emit "主副本   : $Main"
Emit "集成工作树: $Work"
Emit "报告文件 : $report"

# ---------------------------------------------------------------- 1. git 拓扑
Section '1. git 拓扑：工作树、分支、未提交改动'

Run 'git worktree list' { git -C $Main worktree list }
Run '本地分支（含跟踪关系）' { git -C $Main branch -vv }

Run '主副本当前分支' { git -C $Main rev-parse --abbrev-ref HEAD }
Run '主副本未提交改动（前 40 条）' {
    $s = git -C $Main status --porcelain
    if (-not $s) { '（干净）' } else { $s | Select-Object -First 40; "总计 $(($s | Measure-Object).Count) 条" }
}

if (Test-Path $Work) {
    Run '工作树当前分支' { git -C $Work rev-parse --abbrev-ref HEAD }
    Run '工作树未提交改动（前 40 条）' {
        $s = git -C $Work status --porcelain
        if (-not $s) { '（干净）' } else { $s | Select-Object -First 40; "总计 $(($s | Measure-Object).Count) 条" }
    }
    Run '工作树最近 10 次提交' { git -C $Work log --oneline -10 }
} else {
    Emit ''
    Emit "    [注意] 工作树路径不存在：$Work"
}

# ------------------------------------------------------------ 2. 与远端的落差
Section '2. 与 origin/main 的落差'

if (-not $NoFetch) {
    Run 'git fetch origin --prune（只取，不改本地分支）' { git -C $Main fetch origin --prune }
} else {
    Emit ''
    Emit '    （已按 -NoFetch 跳过抓取，落差基于上次 fetch 的缓存）'
}

Run 'origin/main 指向' { git -C $Main rev-parse --short origin/main 2>&1; git -C $Main log -1 --format='%ad %s' --date=short origin/main 2>&1 }

foreach ($br in @('v2r-final-integration', 'closers-g2-split')) {
    Run "落差 origin/main vs $br（左=main独有 右=$br独有）" {
        git -C $Main rev-list --left-right --count "origin/main...$br"
    }
    Run "合并基点 origin/main vs $br" {
        $mb = git -C $Main merge-base origin/main $br
        if ($mb) { git -C $Main log -1 --format='%h %ad %s' --date=short $mb } else { '（无法确定）' }
    }
    Run "$br 相对 origin/main 的改动规模" {
        git -C $Main diff --stat "origin/main...$br" | Select-Object -Last 1
    }
}

# ------------------------------------------------------------ 3. 冲突预演
Section '3. 合并冲突预演（不写工作区，不产生提交）'

Run 'git 版本' { git --version }
Run 'merge-tree 预演：origin/main <- v2r-final-integration' {
    $out = git -C $Main merge-tree --write-tree origin/main v2r-final-integration 2>&1
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        '无冲突：可以直接合并。'
    } elseif ($out -match 'unknown option|usage:') {
        'git 版本过低，不支持 merge-tree --write-tree；请改用：git switch -c 收口试合 origin/main; git merge --no-commit --no-ff v2r-final-integration'
    } else {
        "存在冲突，merge-tree 原始输出（前 60 行）："
        ($out -split "`r?`n") | Select-Object -First 60
    }
}

Run 'closers-g2-split 是否已被 v2r-final-integration 包含' {
    git -C $Main merge-base --is-ancestor closers-g2-split v2r-final-integration 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { '是：closers-g2-split 的提交已全部包含，可直接删除该分支。' }
    else { '否：closers-g2-split 有独立提交，需单独决定是否合并。' }
}

Run '两分支同时改过的文件（潜在冲突面）' {
    $a = git -C $Main diff --name-only "origin/main...v2r-final-integration"
    $b = git -C $Main diff --name-only "origin/main...closers-g2-split"
    $both = Compare-Object $a $b -IncludeEqual -ExcludeDifferent | Select-Object -ExpandProperty InputObject
    if ($both) { $both | Select-Object -First 40; "总计 $(($both | Measure-Object).Count) 个文件被两边同时改过" }
    else { '（无重叠文件）' }
}

# ------------------------------------------------------------ 4. 运行态
Section '4. 运行态：端口、进程、后端身份'

foreach ($p in @(8001, 8123, 3001)) {
    Run "端口 $p 监听情况" {
        $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        if ($c) {
            foreach ($x in $c) {
                $proc = Get-Process -Id $x.OwningProcess -ErrorAction SilentlyContinue
                "PID=$($x.OwningProcess) 进程=$($proc.ProcessName) 路径=$($proc.Path)"
            }
        } else { '（未监听）' }
    }
}

Run '后端身份 /api/v2/platform/status' {
    try {
        $r = Get-JsonUtf8 'http://127.0.0.1:8001/api/v2/platform/status' 8
        $r | ConvertTo-Json -Depth 6
    } catch { "（8001 无响应或路由不存在）$($_.Exception.Message)" }
}

Run '七闸门 /api/v2/readiness' {
    try {
        $r = Get-JsonUtf8 'http://127.0.0.1:8001/api/v2/readiness' 15
        $r | ConvertTo-Json -Depth 5
    } catch { "（无响应）$($_.Exception.Message)" }
}

# ------------------------------------------------------------ 5. 数据与门禁
Section '5. 数据绑定、体积与硬门'

$dbs = @(
    'E:\CODEX\Stock_selection\accumulation_breakout\runtime\stock_data.db',
    'E:\CODEX\Stock_selection\accumulation_breakout\runtime\lhb_product.db',
    'E:\CODEX\Stock_selection\worktrees\v2r-final-integration\runtime\stock_data.db'
)
foreach ($d in $dbs) {
    Emit ''
    if (Test-Path $d) {
        $f = Get-Item $d
        Emit ("    {0,-78} {1,8:N2} GB  最后写入 {2}" -f $d, ($f.Length / 1GB), $f.LastWriteTime)
    } else {
        Emit "    $d  （不存在）"
    }
}

Run 'platform_v2.yaml 的 flags' {
    $y = Join-Path $Work 'configs\platform_v2.yaml'
    if (Test-Path $y) {
        $txt = [System.IO.File]::ReadAllText($y, [System.Text.Encoding]::UTF8)
        ($txt -split "`r?`n") | Where-Object { $_ -match ':\s*(true|false)\s*$' }
    }
    else { '（未找到）' }
}

Run '环境变量中的硬门（应为空或 false）' {
    foreach ($k in 'LIVE_TRADING_ENABLED', 'DAILY_SCHEDULER_ENABLED', 'V2_PIT_READ_ENABLED', 'AB_DB_PATH', 'AB_BACKUP_ROOT') {
        "$k = $([Environment]::GetEnvironmentVariable($k))"
    }
}

Run '最近一次扫描结果（工作树 runtime）' {
    $latest = Get-ChildItem (Join-Path $Work 'runtime') -Filter 'scan_*.result.json' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        "文件: $($latest.Name)  时间: $($latest.LastWriteTime)"
        $j = Read-JsonFile $latest.FullName
        "as_of=$($j.latest_date)  环境=$($j.regime.label)  允许开仓=$($j.regime.allow_new_entries)"
        "A池=$($j.count_a)  B池=$($j.count_b)  候选=$($j.total_candidates)  新鲜度=$($j.freshness.stale_label)"
    } else { '（无扫描结果）' }
}

# ------------------------------------------------------------ 6. 结论提示
Section '6. 读法提示'
Emit '  · 第 2 节右侧数字 = 只存在于本地分支、尚未进 origin/main 的提交数。'
Emit '  · 第 3 节无冲突 → 可按建议顺序直接合并；有冲突 → 先看重叠文件清单。'
Emit '  · 第 4 节 8001 的进程路径决定了「你每天用的是哪份代码」。'
Emit '  · 第 5 节 A池=0 且环境=防守 属于风控预期行为，不是故障。'
Emit ''
Emit "报告已写入：$report"

$lines | Out-File -LiteralPath $report -Encoding utf8
try { [Console]::OutputEncoding = $__prevOut } catch { }
