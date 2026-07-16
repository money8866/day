@echo off
REM ============================================================
REM ETF Alpha Engine Daily Run (Post-market)
REM Recommended time: 16:00 on each trading day
REM ============================================================
REM Usage:
REM   1. Manual: double-click this file
REM   2. Scheduled: via Windows Task Scheduler
REM ============================================================

setlocal enabledelayedexpansion

REM ===== Config =====
set "WORK_DIR=d:\mystock\solo\etf_alpha_engine"
set "PYTHON_EXE=python"
set "LOG_DIR=%WORK_DIR%\logs"
set "D=%date:~0,4%%date:~5,2%%date:~8,2%"
set "T=%time:~0,2%%time:~3,2%%time:~6,2%"
set "T=%T: =0%"
set "LOG_FILE=%LOG_DIR%\run_%D%_%T%.log"

REM ===== Create log dir =====
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ===== Start =====
echo ============================================================ > "%LOG_FILE%"
echo ETF Alpha Engine Daily Run >> "%LOG_FILE%"
echo Start: %date% %time% >> "%LOG_FILE%"
echo WorkDir: %WORK_DIR% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

cd /d "%WORK_DIR%"

REM ===== Step 1: Run ETF Alpha Engine =====
echo [1/2] Running ETF Alpha Engine (main.py)... >> "%LOG_FILE%"
echo [1/2] Running ETF Alpha Engine (main.py)...
echo ------------------------------------------------------------ >> "%LOG_FILE%"
"%PYTHON_EXE%" main.py >> "%LOG_FILE%" 2>&1
set "STEP1_EXIT=!errorlevel!"
echo. >> "%LOG_FILE%"
echo Step1 exit code: !STEP1_EXIT! >> "%LOG_FILE%"
echo.

if !STEP1_EXIT! neq 0 (
    echo [WARN] main.py failed exit=!STEP1_EXIT!, continue to report... >> "%LOG_FILE%"
    echo [WARN] main.py failed exit=!STEP1_EXIT!, continue to report...
    echo. >> "%LOG_FILE%"
)

REM ===== Step 2: DeepSeek report and push to WeChat =====
echo [2/2] DeepSeek AI report and push to WeChat... >> "%LOG_FILE%"
echo [2/2] DeepSeek AI report and push to WeChat...
echo ------------------------------------------------------------ >> "%LOG_FILE%"
"%PYTHON_EXE%" report_sender.py >> "%LOG_FILE%" 2>&1
set "STEP2_EXIT=!errorlevel!"
echo. >> "%LOG_FILE%"
echo Step2 exit code: !STEP2_EXIT! >> "%LOG_FILE%"
echo.

REM ===== Summary =====
echo ============================================================ >> "%LOG_FILE%"
echo Summary >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"
echo   main.py:          exit !STEP1_EXIT! >> "%LOG_FILE%"
echo   report_sender.py: exit !STEP2_EXIT! >> "%LOG_FILE%"
echo End: %date% %time% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

echo.
echo ============================================================
echo Summary
echo ============================================================
echo   main.py:          exit !STEP1_EXIT!
echo   report_sender.py: exit !STEP2_EXIT!
echo End: %date% %time%
echo Log: %LOG_FILE%
echo ============================================================

REM ===== Keep only last 30 days of logs =====
forfiles /p "%LOG_DIR%" /m "run_*.log" /d -30 /c "cmd /c del @file" 2>nul

if "%1"=="-no-pause" goto :end
pause

:end
endlocal
