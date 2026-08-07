@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 强制结束占用 8000 端口的进程并重启后端 ===
echo.

REM 需要管理员权限才能杀掉某些残留进程
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo [提示] 当前不是管理员。若结束失败，请右键「以管理员身份运行」本 bat。
  echo.
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo 结束 PID %%a ...
  taskkill /F /T /PID %%a 2>nul
)

timeout /t 2 /nobreak >nul

REM 确认端口
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
  echo [失败] 端口 8000 仍被占用。请打开任务管理器结束对应 python.exe，或右键本脚本「以管理员身份运行」。
  pause
  exit /b 1
)

echo 端口已释放，启动后端...
if not exist runtime mkdir runtime
start "AB-Screener-Backend" /MIN C:\Python314\python.exe web\backend_app.py
timeout /t 3 /nobreak >nul

curl -s http://127.0.0.1:8000/api/health
echo.
echo.
echo 若上面显示 status ok，即可打开 http://127.0.0.1:8000/
pause
