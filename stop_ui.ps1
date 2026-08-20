# Stop only AB-Screener-owned UI processes. Never reclaim another application's port.
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root 'runtime'
$NormalizedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\').ToLowerInvariant()

function Test-OwnedProcess([int]$ProcessId, [string]$Kind) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $process -or -not $process.CommandLine) { return $false }
  $commandLine = $process.CommandLine.ToLowerInvariant()
  if (-not $commandLine.Contains($NormalizedRoot)) { return $false }
  if ($Kind -eq 'backend') { return $commandLine.Contains('backend_app.py') }
  if ($Kind -eq 'frontend') {
    return $commandLine.Contains('web\frontend') -or $commandLine.Contains('vite') -or $commandLine.Contains('npm run dev')
  }
  return $false
}

function Stop-PidFile([string]$Name, [string]$Kind) {
  $path = Join-Path $LogDir $Name
  if (-not (Test-Path -LiteralPath $path)) { return }
  $value = (Get-Content -LiteralPath $path -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
  if ($value -match '^\d+$') {
    $processId = [int]$value
    if (Test-OwnedProcess $processId $Kind) {
      & taskkill /PID $processId /T /F 2>$null | Out-Null
      Write-Host "stopped $Kind pid=$processId"
    } else {
      Write-Host "ignored stale or unowned $Kind pid=$processId" -ForegroundColor Yellow
    }
  }
  Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}

function Stop-OwnedListener([int]$Port, [string]$Kind) {
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    $processId = $_.OwningProcess
    if (Test-OwnedProcess $processId $Kind) {
      & taskkill /PID $processId /T /F 2>$null | Out-Null
      Write-Host "freed AB $Kind port $Port pid=$processId"
    } else {
      Write-Host "left unowned port $Port pid=$processId untouched" -ForegroundColor Yellow
    }
  }
}

Stop-PidFile 'backend.pid' 'backend'
Stop-PidFile 'frontend.pid' 'frontend'
Stop-OwnedListener 8001 'backend'
Stop-OwnedListener 3001 'frontend'

Write-Host 'done'
