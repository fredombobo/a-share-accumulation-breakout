@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 停止选股系统
echo 正在停止服务…
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_ui.ps1"
echo 完成。
timeout /t 2 >nul
