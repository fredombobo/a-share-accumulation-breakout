<#
收口合并 —— 把抢救分支的龙虎榜工作合进集成分支。

策略（已确认）：
  · dist/ 生成物         → 整个取集成分支，合完重新 npm run build 覆盖
  · Sidebar.tsx          → 取集成分支（8001 导航不上架龙虎榜）
  · App.tsx / core.ts    → 保留人工解决（路由要留，导航不留）
  · 后端与测试 7 个文件   → 保留人工解决（两边改动都必须活下来）

本脚本只做机械部分，然后**停下**，把还需要人看的文件列出来。
不提交、不推送。随时可以 git merge --abort 退回原状。
#>

param(
    [string]$Main   = 'E:\CODEX\Stock_selection\accumulation_breakout',
    [string]$Base   = 'v2r-final-integration',
    [string]$Bring  = 'lhb-rescue-20260831',
    [string]$Branch = '收口-20260831'
)

$ErrorActionPreference = 'Continue'
$__prevOut = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Say([string]$t, [string]$c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Die([string]$t) { Write-Host ''; Say "X  $t" 'Red'; Write-Host ''; exit 1 }
function Line { Write-Host ('-' * 66) -ForegroundColor DarkGray }

Set-Location -LiteralPath $Main

Write-Host ''
Say "收口合并  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 'White'
Say "  基底 $Base"
Say "  并入 $Bring"
Say "  新分支 $Branch"

# ------------------------------------------------ 前置检查
Line
if (git status --porcelain) { Die '工作区不干净。先提交或清理，再跑本脚本。' }
if (Test-Path (Join-Path $Main '.git\MERGE_HEAD')) { Die '当前已在一次合并中。先 git merge --abort 再重来。' }
git show-ref --verify --quiet "refs/heads/$Branch"
if ($LASTEXITCODE -eq 0) { Die "分支 $Branch 已存在。换个名字，或先删掉它。" }
Say 'OK 工作区干净，无进行中的合并' 'Green'

# ------------------------------------------------ 建分支
Line
git switch -c $Branch $Base
if ($LASTEXITCODE -ne 0) { Die "无法从 $Base 建分支。" }
Say "OK 已切到 $Branch（内容 = $Base）" 'Green'

# ------------------------------------------------ 起合并（预期冲突）
Line
Say "合并 $Bring …" 'Cyan'
git merge --no-ff --no-commit $Bring 2>&1 | Out-Null
if (-not (Test-Path (Join-Path $Main '.git\MERGE_HEAD'))) {
    Say '合并没有进入冲突状态——可能已干净合并。检查 git status 后自行提交。' 'Yellow'
    try { [Console]::OutputEncoding = $__prevOut } catch { }
    exit 0
}
Say '已进入合并状态（有冲突，符合预期）' 'Yellow'

# ------------------------------------------------ 自动解 1：dist 全取集成分支
Line
Say '自动解 1/2：web/frontend/dist 整体取集成分支' 'Cyan'
git rm -r -f -q --ignore-unmatch -- web/frontend/dist 2>&1 | Out-Null
git checkout $Base -- web/frontend/dist 2>&1 | Out-Null
git add -A -- web/frontend/dist 2>&1 | Out-Null
Say '     dist 已对齐到集成分支（合完记得 npm run build 重建）' 'Green'

# ------------------------------------------------ 自动解 2：Sidebar 取集成分支
Line
Say '自动解 2/2：Sidebar.tsx 取集成分支（8001 导航不上架龙虎榜）' 'Cyan'
git checkout $Base -- web/frontend/src/layout/Sidebar.tsx 2>&1 | Out-Null
git add -- web/frontend/src/layout/Sidebar.tsx 2>&1 | Out-Null
Say '     Sidebar.tsx 已取集成分支版本' 'Green'

# ------------------------------------------------ 剩余
Line
$remain = @(git diff --name-only --diff-filter=U)
if ($remain.Count -eq 0) {
    Say '所有冲突已解决。检查 git status 后提交：' 'Green'
    Say "    git commit -m `"merge: 并入龙虎榜 T01-T12（8001 导航不上架）`"" 'White'
} else {
    Say ("还剩 {0} 个文件需要人工解决：" -f $remain.Count) 'Yellow'
    Write-Host ''
    $remain | ForEach-Object { Write-Host "    $_" }
    Write-Host ''
    Say '这些文件里现在带着 <<<<<<< ======= >>>>>>> 冲突标记。' 'DarkGray'
    Say '解完每个文件后 git add，全部解完再 git commit。' 'DarkGray'
}

Line
Say '想退回原状（随时可用，不会丢东西）：' 'DarkGray'
Say "    git merge --abort;  git switch lhb-rescue-20260831;  git branch -D $Branch" 'DarkGray'
Write-Host ''

try { [Console]::OutputEncoding = $__prevOut } catch { }
