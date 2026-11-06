@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 门诊预约系统 · 客户端

echo ============================================================
echo   智慧医疗 · 社区门诊预约与处方管理系统
echo   B/S 客户端（Windows）   连到   MySQL（Ubuntu 服务器）
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 没找到虚拟环境 .venv
  echo.
  echo 请在本目录下执行一次：
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo 正在启动，稍等几秒...
echo 启动后会自动打开浏览器页面，关闭本窗口即可停止服务。
echo.

start "" http://127.0.0.1:5000
.venv\Scripts\python.exe run.py

echo.
echo 服务已停止。
pause
