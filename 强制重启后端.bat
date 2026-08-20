@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 强制结束占用 8001 端口的本项目后端并重启 ===
echo.

REM 需要管理员权限才能杀掉某些残留进程
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo [提示] 当前不是管理员。若结束失败，请右键「以管理员身份运行」本 bat。
  echo.
)

REM 仅结束属于本项目的后端（命令行含 backend_app.py），绝不误杀其它应用
REM （2026-08-16 整改：本项目端口已迁移到 8001；8000 固定留给 AETF Alpha）
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING"') do (
  powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%%a' -ErrorAction SilentlyContinue; if($p -and $p.CommandLine -like '*backend_app.py*'){exit 0}else{exit 1}"
  if not errorlevel 1 (
    echo 结束本项目后端 PID %%a ...
    taskkill /F /T /PID %%a 2>nul
  ) else (
    echo [跳过] 端口 8001 的 PID %%a 不属于本项目后端（不终止）
  )
)

timeout /t 2 /nobreak >nul

REM 确认端口
netstat -ano | findstr ":8001" | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
  echo [失败] 端口 8001 仍被占用。请运行 stop_ui.ps1 或任务管理器排查。
  pause
  exit /b 1
)

echo 端口已释放，启动后端（8001）...
if not exist runtime mkdir runtime
set AB_BACKEND_PORT=8001
start "AB-Screener-Backend" /MIN python web\backend_app.py
timeout /t 3 /nobreak >nul

curl -s http://127.0.0.1:8001/api/health
echo.
echo.
echo 若上面显示 status ok，即可打开 http://127.0.0.1:8001/
pause
