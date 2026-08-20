@echo off
chcp 65001 >nul
setlocal
rem ============================================================
rem  Logic Platform - One-click launch (backend + research console)
rem  Usage: double click or run from cmd
rem  （2026-08-16 整改：端口 8001；不再终止 8000 端口上的其它应用）
rem ============================================================
cd /d %~dp0

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Please install Python 3.11+.
  pause
  exit /b 1
)

echo [1/3] Checking backend on port 8001 ...
set AB_BACKEND_PORT=8001
netstat -ano | findstr ":8001" | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
  echo   [ok] backend already listening on 8001 (will not stop it)
) else (
  echo   starting backend: web\backend_app.py ...
  if not exist runtime mkdir runtime
  start "AB-Logic-Backend" /MIN python web\backend_app.py
  timeout /t 5 /nobreak >nul
)

echo [2/3] Opening research console ...
if exist "%~dp0runtime\logic_console.html" (
  start "" "%~dp0runtime\logic_console.html"
) else (
  echo   [warn] runtime\logic_console.html not found - use http://127.0.0.1:8001/ instead
)

echo [3/3] Done.
echo.
echo  Backend : http://127.0.0.1:8001
echo  Health  : http://127.0.0.1:8001/api/logic/health
echo  NOTE    : research_only - signals are research, not trading advice
endlocal
pause
