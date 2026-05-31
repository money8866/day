@echo off
title Realtime Theme Monitor
echo ========================================
echo    Realtime Theme Monitor
echo ========================================
echo.

cd /d "d:\mystock\solo"

python realtime_theme_monitor.py

if errorlevel 1 (
    echo.
    echo Program exited abnormally, check console for details...
    pause >nul
)
