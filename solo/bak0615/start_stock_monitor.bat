@echo off
chcp 65001 >nul
title Stock Monitor - Startup Script

set SCRIPT_DIR=C:\Users\kongx\mystock\dayreal
set LOG_DIR=C:\Users\kongx\mystock\solo\logs
set MAIN_PY=%SCRIPT_DIR%\main.py
set PYTHON_EXE=C:\Users\kongx\AppData\Local\Python\bin\python.exe

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "YEAR=%date:~0,4%"
set "MONTH=%date:~5,2%"
set "DAY=%date:~8,2%"
set "HOUR=%time:~0,2%"
set "MINUTE=%time:~3,2%"
set "SECOND=%time:~6,2%"
set HOUR=%HOUR: =0%
set MINUTE=%MINUTE: =0%
set SECOND=%SECOND: =0%

set LOG_FILE=%LOG_DIR%\stock_monitor_%YEAR%%MONTH%%DAY%_%HOUR%%MINUTE%%SECOND%.log

echo ========================================================
echo Stock Monitor - Starting...
echo Start Time: %date% %time%
echo Log File: %LOG_FILE%
echo ========================================================
echo.

cd /d "%SCRIPT_DIR%"

"%PYTHON_EXE%" main.py > "%LOG_FILE%" 2>&1

echo.
echo Program exited
pause
