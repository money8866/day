@echo off
chcp 65001 >nul

cd /d "C:\Users\kongx\mystock\dayreal"

set "YEAR=%date:~0,4%"
set "MONTH=%date:~5,2%"
set "DAY=%date:~8,2%"
set "HOUR=%time:~0,2%"
set "MINUTE=%time:~3,2%"
set "SECOND=%time:~6,2%"
set HOUR=%HOUR: =0%
set MINUTE=%MINUTE: =0%
set SECOND=%SECOND: =0%

set LOG_FILE=C:\Users\kongx\mystock\solo\logs\stock_monitor_%YEAR%%MONTH%%DAY%_%HOUR%%MINUTE%%SECOND%.log

echo ========================================================
echo Stock Monitor - Starting...
echo Start Time: %date% %time%
echo Log File: %LOG_FILE%
echo ========================================================
echo.

C:\Users\kongx\AppData\Local\Python\bin\python.exe main.py > "%LOG_FILE%" 2>&1
