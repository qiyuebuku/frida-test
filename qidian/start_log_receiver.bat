@echo off
REM 起点读书 Hook - 日志接收器启动脚本
REM 请在 Windows 宿主机上运行此脚本

echo ========================================
echo   起点读书 Hook - 日志接收器
echo ========================================
echo.

REM 切换到脚本所在目录
cd /d %~dp0

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3
    pause
    exit /b 1
)

REM 显示本机 IP 地址
echo [1] 获取本机 IP 地址...
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set IP=%%a
    set IP=!IP: =!
    echo     本机 IP: !IP!
)

echo.
echo [2] 启动日志接收器...
echo     监听端口: 8889
echo     日志目录: %cd%\logs
echo.
echo ========================================
echo   提示：请确保 Android 端配置的 IP 地址正确
echo   当前 IP: !IP!
echo ========================================
echo.

REM 启动日志接收器
python scripts\log_receiver.py

pause
