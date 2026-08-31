@echo off
REM Read-only repository / runtime health check before merging the integration branch.
setlocal
set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%closeout_check.ps1" %*
echo.
pause
