@echo off
REM Daily entry for the accumulation-breakout screener.
REM Double-click this file. Logic lives in daily_run.ps1 next to it.
setlocal
set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%daily_run.ps1" %*
echo.
pause
