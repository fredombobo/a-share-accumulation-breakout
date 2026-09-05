@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AB-Screener - One-click start
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH="

REM ASCII-only shell wrapper. Python prints the Chinese instructions.
REM Shared runtime selection validates 3.12 before any pip installation.
if exist ".venv312\Scripts\python.exe" (
  set "AB_LAUNCH_PY=%~dp0.venv312\Scripts\python.exe"
  goto launch
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3.12 -c "import sys; sys.exit(sys.version_info[:2] != (3,12))" >nul 2>&1
  if not errorlevel 1 (
    py -3.12 easy_start.py %*
    goto result
  )
)
where python >nul 2>&1
if not errorlevel 1 (
  set "AB_LAUNCH_PY=python"
  goto launch
)
if exist "C:\Python312\python.exe" (
  set "AB_LAUNCH_PY=C:\Python312\python.exe"
  goto launch
)
echo [ERROR] Python 3.12 was not found. Install Python 3.12 and retry.
set "AB_START_EXIT=1"
goto finish

:launch
"%AB_LAUNCH_PY%" easy_start.py %*
:result
set "AB_START_EXIT=%ERRORLEVEL%"
:finish
if not "%AB_START_EXIT%"=="0" echo [ERROR] Startup failed. Please keep the error shown above.
if not defined AB_START_NO_PAUSE pause
exit /b %AB_START_EXIT%
