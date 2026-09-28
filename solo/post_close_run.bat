@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Post-close pipeline: backfill -^> T+3 screen -^> feishu -^> db sync
rem Post-close pipeline: tracking backfill, T+3 execution screen, feishu push, db sync.
rem Usage: post_close_run.bat [--date YYYYMMDD] [--live] [--print] [--no-push] [--no-sync]
rem Remaining args are passed through to t3_screen_build.py and t3_report_push.py.
rem --no-push skips the feishu push step.
rem --no-sync skips the stock_picks.db sync to the cloud server.
cd /d %~dp0

rem Strip our own switches out of the arg list, pass the rest through.
set "PUSH=1"
set "SYNC=1"
set "ARGS="
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-push" set "PUSH=0"
if /i "%~1"=="--no-sync" set "SYNC=0"
if /i not "%~1"=="--no-push" if /i not "%~1"=="--no-sync" set "ARGS=%ARGS% %~1"
shift
goto parse_args
:args_done

echo ============================================================
echo   [1/4] Backfill tracking returns
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
echo   [3/4] Push report to Feishu
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
echo ============================================================
echo   [4/4] Sync stock_picks.db to cloud server
echo ============================================================
if "%SYNC%"=="0" (
    echo [SKIP] --no-sync
) else (
    python pick_web\sync_db.py
    if errorlevel 1 (
        echo.
        echo [WARN] db sync failed, local report is unaffected
    )
)

echo.
echo Done.
echo   report: t3_screen\output\t3_report_*.md
echo   data  : t3_screen\output\t3_screen_*.json
echo   web   : pick_web\  ^(see pick_web\sync_config.json for server^)
echo.
pause
