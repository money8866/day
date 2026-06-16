@echo off
chcp 65001 >nul
title Stock Monitor - Log Viewer

set LOG_DIR=C:\Users\kongx\mystock\solo\logs

if not exist "%LOG_DIR%" (
    echo Log directory does not exist: %LOG_DIR%
    exit /b 1
)

echo ========================================================
echo Stock Monitor - Log Viewer
echo ========================================================
echo.

dir /b /o-d "%LOG_DIR%\stock_monitor_*.log" | find /c /v ""

echo.
echo Available log files (sorted by date, newest first):
echo.

set COUNT=0
for /f "tokens=1,* delims==" %%a in ('dir /b /o-d "%LOG_DIR%\stock_monitor_*.log" 2^>nul') do (
    set /a COUNT+=1
    echo   !COUNT!. %%a
)

echo.
if %COUNT% EQU 0 (
    echo No log files found in %LOG_DIR%
    echo.
    echo Press any key to exit...
    pause >nul
    exit /b 1
)

echo Enter the number of the log file to view (1-%COUNT%), or Q to quit:
set /p choice=

if /i "%choice%"=="Q" exit /b 0

if %choice% LSS 1 (
    echo Invalid choice
    exit /b 1
)

if %choice% GTR %COUNT% (
    echo Invalid choice
    exit /b 1
)

set FILE_NUM=0
set LOG_FILE=
for /f "tokens=1,* delims==" %%a in ('dir /b /o-d "%LOG_DIR%\stock_monitor_*.log" 2^>nul') do (
    set /a FILE_NUM+=1
    if !FILE_NUM!==%choice% (
        set LOG_FILE=%%a
        goto :found
    )
)

:found
if not defined LOG_FILE (
    echo Log file not found
    exit /b 1
)

echo.
echo ========================================================
echo Viewing: %LOG_FILE%
echo ========================================================
echo.

type "%LOG_FILE%"

echo.
echo ========================================================
echo End of log file
echo ========================================================
echo.
pause

