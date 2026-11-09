@echo off
title 门诊预约系统 · Linux 数据库服务器（Ubuntu 24.04）

echo ============================================================
echo   智慧医疗 · 社区门诊预约系统 —— Linux 数据库服务器
echo   作用：唤醒 WSL Ubuntu，启动里面的 MySQL，并登记它的 IP
echo ============================================================
echo.

echo [1/3] 唤醒 Ubuntu（首次约需 10~30 秒，请耐心等）...
wsl.exe -d Ubuntu-24.04 -e true 2>nul
if errorlevel 1 (
  echo.
  echo [错误] 无法启动 Ubuntu-24.04。
  echo        请确认开始菜单里有 "Ubuntu 24.04"，或先手动打开它一次。
  echo.
  pause
  exit /b 1
)
echo       Ubuntu 已启动。

echo.
echo [2/3] 启动 MySQL（以 root 执行，无需密码）...
wsl.exe -d Ubuntu-24.04 -u root -e sh -c "pgrep -x mysqld >/dev/null 2>&1 && echo '      MySQL 已在运行，跳过启动' || service mysql start"
if errorlevel 1 (
  echo.
  echo [错误] MySQL 启动失败。
  echo        可打开 Ubuntu 窗口手动执行: sudo service mysql start
  echo        然后查看日志: tail -30 /var/log/mysql/error.log
  echo.
  pause
  exit /b 1
)

echo       等待端口 3306 就绪...
set /a n=0
:waitloop
set /a n+=1
wsl.exe -d Ubuntu-24.04 -e sh -c "ss -tln 2>/dev/null | grep -q ':3306 '"
if errorlevel 1 (
  if %n% lss 15 (
    ping -n 3 127.0.0.1 >nul
    goto waitloop
  )
  echo [错误] 3306 端口一直未就绪，MySQL 可能启动失败。
  echo        请打开 Ubuntu 窗口执行: tail -30 /var/log/mysql/error.log
  echo.
  pause
  exit /b 1
)
echo       3306 已就绪。

echo.
echo [3/3] 登记服务器 IP（供客户端自动发现）...
set WSLIP=
for /f "tokens=1" %%i in ('wsl.exe -d Ubuntu-24.04 -e sh -c "hostname -I"') do set WSLIP=%%i
if "%WSLIP%"=="" (
  echo [警告] 未能取得 Ubuntu 的 IP，客户端将改用网段扫描（稍慢但也能找到）。
) else (
  >"%~dp0wsl_ip.txt" echo %WSLIP%
  wsl.exe -d Ubuntu-24.04 -u root -e sh -c "echo %WSLIP% > /home/liaoyilin/wsl_ip.txt; chown liaoyilin:liaoyilin /home/liaoyilin/wsl_ip.txt"
  echo       服务器 IP = %WSLIP%  （已写入本目录 wsl_ip.txt）
)

echo.
echo ============================================================
echo   服务器已就绪！
echo   下一步：双击「启动客户端.bat」，浏览器会自动打开系统页面。
echo.
echo   注意：本窗口可以最小化，但不要关闭 ——
echo         WSL 虚拟机会在最后一个窗口关闭后自动关机，MySQL 随之停止。
echo ============================================================
echo.
pause
