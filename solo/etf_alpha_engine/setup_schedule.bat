@echo off
REM ============================================================
REM Create Windows Scheduled Task: ETF_Alpha_Daily at 16:00
REM Run this script as Administrator
REM ============================================================

set "TASK_NAME=ETF_Alpha_Daily"
set "BAT_PATH=d:\mystock\solo\etf_alpha_engine\daily_run.bat"

echo Creating scheduled task "%TASK_NAME%" ...
echo   Program: %BAT_PATH%
echo   Time:    16:00 daily
echo.

REM Create task (daily at 16:00)
schtasks /create /tn "%TASK_NAME%" /tr "%BAT_PATH% -no-pause" /sc daily /st 16:00 /f

if %errorlevel% equ 0 (
    echo.
    echo [OK] Scheduled task created successfully.
    echo.
    echo Management commands:
    echo   Query:  schtasks /query /tn "%TASK_NAME%"
    echo   Run:    schtasks /run /tn "%TASK_NAME%"
    echo   Delete: schtasks /delete /tn "%TASK_NAME%" /f
    echo.
) else (
    echo.
    echo [FAIL] Failed to create task. Please run as Administrator.
    echo.
)

pause
