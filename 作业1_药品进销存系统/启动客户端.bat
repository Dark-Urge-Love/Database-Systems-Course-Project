@echo off
title 门诊预约系统 · 一键启动
cd /d "%~dp0"

echo ============================================================
echo   智慧医疗 · 社区门诊预约与处方管理系统
echo   本脚本自动完成：唤醒 Ubuntu → 启动 MySQL → 启动客户端
echo ============================================================
echo.

echo [1/6] 检查虚拟环境...
if not exist ".venv\Scripts\python.exe" (
  echo [错误] 没找到虚拟环境 .venv
  echo        请在本目录执行一次:
  echo        python -m venv .venv
  echo        .venv\Scripts\pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)
echo       虚拟环境 OK。

echo.
echo [2/6] 清理上一次可能残留的客户端进程...
set CLEANED=0
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| find "127.0.0.1:5000" ^| find "LISTENING"') do (
  taskkill /f /pid %%p >nul 2>&1 && set CLEANED=1
)
if "%CLEANED%"=="1" (
  echo       已停止旧客户端实例。
  ping -n 3 127.0.0.1 >nul
) else (
  echo       无残留进程。
)

echo.
echo [3/6] 唤醒 Ubuntu 数据库服务器（首次约需 10~30 秒）...
wsl.exe -d Ubuntu-24.04 -e true 2>nul
if errorlevel 1 (
  echo [错误] 无法启动 Ubuntu-24.04。
  echo        请确认开始菜单里安装了 "Ubuntu 24.04"。
  echo.
  pause
  exit /b 1
)

echo.
echo [4/6] 启动 MySQL（root 执行，无需密码）...
wsl.exe -d Ubuntu-24.04 -u root -e sh -c "pgrep -x mysqld >/dev/null 2>&1 && echo '      MySQL 已在运行，跳过' || service mysql start"
set /a n=0
:waitdb
set /a n+=1
wsl.exe -d Ubuntu-24.04 -e sh -c "ss -tln 2>/dev/null | grep -q ':3306 '"
if errorlevel 1 (
  if %n% lss 15 (
    ping -n 3 127.0.0.1 >nul
    goto waitdb
  )
  echo [错误] MySQL 端口 3306 一直未就绪。
  echo        可打开 Ubuntu 窗口执行: tail -30 /var/log/mysql/error.log
  echo.
  pause
  exit /b 1
)
echo       数据库就绪。

echo.
echo [5/6] 登记服务器 IP（供客户端自动发现）...
set WSLIP=
for /f "tokens=1" %%i in ('wsl.exe -d Ubuntu-24.04 -e sh -c "hostname -I"') do set WSLIP=%%i
if "%WSLIP%"=="" (
  echo [警告] 未取得 Ubuntu IP，客户端将改用网段扫描（稍慢）。
) else (
  >"%~dp0wsl_ip.txt" echo %WSLIP%
  wsl.exe -d Ubuntu-24.04 -u root -e sh -c "echo %WSLIP% > /home/liaoyilin/wsl_ip.txt; chown liaoyilin:liaoyilin /home/liaoyilin/wsl_ip.txt"
  echo       服务器 IP = %WSLIP%
)

echo.
echo [6/6] 启动 Ubuntu 保活进程（最小化窗口，防止数据库自动关机）...
start "WSL 保活进程 - 请勿关闭" /min wsl.exe -d Ubuntu-24.04 -e sleep infinity

echo.
echo 正在启动客户端（在最小化窗口运行，首次连接约需 5~15 秒）...
start "门诊预约系统客户端 - 关闭此窗口即停止服务" /min .venv\Scripts\python.exe run.py

echo       等待客户端就绪...
set /a m=0
:waitweb
set /a m+=1
netstat -ano 2>nul | find "127.0.0.1:5000" | find "LISTENING" >nul
if errorlevel 1 (
  if %m% lss 30 (
    ping -n 2 127.0.0.1 >nul
    goto waitweb
  )
  echo [错误] 客户端 30 秒内未就绪。
  echo        请查看最小化的"门诊预约系统客户端"窗口里的报错信息。
  echo.
  pause
  exit /b 1
)

start "" http://127.0.0.1:5000
echo.
echo ============================================================
echo   启动完成！浏览器已打开 http://127.0.0.1:5000
echo.
echo   运行中的两个最小化窗口：
echo     [1] "WSL 保活进程"      —— 关闭它 = 数据库服务器停止
echo     [2] "门诊预约系统客户端" —— 关闭它 = 网页服务停止
echo   （任务栏里可以找到它们，平时最小化即可）
echo ============================================================
echo.
echo 本窗口可以关闭了。
timeout /t 4 >nul
