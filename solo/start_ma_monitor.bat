@echo off
chcp 65001 >nul
echo ========================================
echo    实时均线监控启动
echo ========================================
echo.

cd /d "d:\mystock\solo"

python realtime_ma_monitor.py

if errorlevel 1 (
    echo.
    echo 程序异常退出，按任意键关闭...
    pause >nul
)
