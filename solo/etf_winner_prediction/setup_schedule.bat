@echo off
cd /d "%~dp0"
echo ============================================
echo  ETF Winner Prediction - Schedule Setup
echo ============================================
echo.
echo This script sets up a daily scheduled task (Windows Task Scheduler)
echo to run the ETF Winner Prediction Engine at 16:00 every trading day.
echo.

set TASK_NAME=ETFWinnerPredictionDaily
set SCRIPT_PATH=%~dp0daily_run.bat

echo Creating scheduled task: %TASK_NAME%
echo Script path: %SCRIPT_PATH%

schtasks /create /tn "%TASK_NAME%" /tr "%SCRIPT_PATH%" /sc daily /st 16:00 /f

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Scheduled task created successfully.
    echo Task will run daily at 16:00.
    echo.
    echo To remove: schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo.
    echo Failed to create scheduled task. Please run as Administrator.
)

pause