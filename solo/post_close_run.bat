@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Post-close pipeline: backfill -^> T+3 screen -^> feishu
rem Post-close pipeline: tracking backfill, T+3 execution screen, feishu push.
rem Usage: post_close_run.bat [--date YYYYMMDD] [--live] [--print] [--no-push]
rem Remaining args are passed through to t3_screen_build.py and t3_report_push.py.
rem --no-push skips the feishu push step.
cd /d %~dp0

set "PUSH=1"
set "ARGS=%*"
if not "%ARGS%"=="%ARGS:--no-push=%" set "PUSH=0"
set "ARGS=%ARGS:--no-push=%"

echo ============================================================
echo   [1/3] Backfill tracking returns
echo ============================================================
python stock_pick_db.py tracking
if errorlevel 1 (
    echo.
    echo [WARN] backfill did not finish cleanly, continue anyway
    echo        T+3 screen reads stock_pick and cache_daily only, not pick_tracking
)

echo.
echo ============================================================
echo   [2/3] T+3 execution screen
echo ============================================================
python t3_screen\t3_screen_build.py %ARGS%
if errorlevel 1 (
    echo.
    echo [ERROR] T+3 screen failed
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   [3/3] Push report to Feishu
echo ============================================================
if "%PUSH%"=="0" (
    echo [SKIP] --no-push
) else (
    python t3_screen\t3_report_push.py %ARGS%
    if errorlevel 1 (
        echo.
        echo [WARN] feishu push failed, report file is still available locally
    )
)

echo.
echo Done.
echo   report: t3_screen\output\t3_report_*.md
echo   data  : t3_screen\output\t3_screen_*.json
echo.
pause
