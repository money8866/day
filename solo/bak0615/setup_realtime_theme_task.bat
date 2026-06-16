@echo off
title Realtime Theme Monitor - Task Setup

echo ========================================
echo    Realtime Theme Monitor - Task Setup
echo ========================================
echo.
echo This script requires Administrator privileges...

net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Administrator privileges required!
    echo Please right-click this file and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo [OK] Administrator privileges obtained
echo.

:: Delete old tasks
schtasks /delete /tn "RealtimeThemeMonitor_Start" /f >nul 2>&1
schtasks /delete /tn "RealtimeThemeMonitor_Stop" /f >nul 2>&1

:: Create start task (9:25 weekdays)
echo [1/2] Creating start task (9:25 weekdays)...
schtasks /create /tn "RealtimeThemeMonitor_Start" /tr "d:\mystock\solo\start_realtime_theme_monitor.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:25 /rl highest /f
if %errorlevel% equ 0 (
    echo   [OK] Start task created
) else (
    echo   [FAIL] Start task creation failed
)

:: Create stop task (15:01 weekdays)
echo.
echo [2/2] Creating stop task (15:01 weekdays)...
schtasks /create /tn "RealtimeThemeMonitor_Stop" /tr "d:\mystock\solo\stop_realtime_theme_monitor.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:01 /rl highest /f
if %errorlevel% equ 0 (
    echo   [OK] Stop task created
) else (
    echo   [FAIL] Stop task creation failed
)

echo.
echo ========================================
echo    Setup Complete!
echo ========================================
echo.
echo Created tasks:
echo   RealtimeThemeMonitor_Start  - 9:25 start
echo   RealtimeThemeMonitor_Stop   - 15:01 stop
echo.
echo Management:
echo   View: schtasks /query /tn RealtimeThemeMonitor_Start
echo   Delete: schtasks /delete /tn "RealtimeThemeMonitor_*" /f
echo.
pause
