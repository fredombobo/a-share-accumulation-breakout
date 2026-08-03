@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 横盘吸筹选股 - 一键启动
echo.
echo  ========================================
echo    横盘吸筹选股系统  ·  小白一键启动
echo  ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  if exist "C:\Python314\python.exe" (
    set "PY=C:\Python314\python.exe"
  ) else if exist "C:\Python313\python.exe" (
    set "PY=C:\Python313\python.exe"
  ) else (
    echo [错误] 未找到 Python。请先安装 Python 3.11+ 并勾选 Add to PATH
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
  )
) else (
  set "PY=python"
)

"%PY%" easy_start.py
if errorlevel 1 (
  echo.
  echo 启动失败，请把上面的报错截图保存。
  pause
  exit /b 1
)
pause
