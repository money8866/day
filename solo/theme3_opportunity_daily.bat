@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title theme3 opportunity daily report (18:30 on trading days)
rem Called by Windows Task Scheduler: build constituents, then heat + opportunity report, then email.
rem Non-trading days are skipped by --if-today so no duplicate mail is sent.
rem Usage: theme3_opportunity_daily.bat [extra args passed to theme3_opportunity.py]
cd /d %~dp0

if not exist report_daily mkdir report_daily
set "LOG=report_daily\theme3_opportunity_daily.log"
echo. >> "%LOG%"
echo ===== %date% %time% ===== >> "%LOG%"

echo [1/2] Building theme3 constituents ...
python -X utf8 theme3_constituents_v2.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] theme3_constituents_v2.py failed, abort without mail. >> "%LOG%"
    exit /b 1
)

echo [2/2] Building heat + opportunity report and sending mail ...
python -X utf8 theme3_opportunity.py --if-today %* >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] theme3_opportunity.py failed. >> "%LOG%"
    exit /b 1
)
echo Done. >> "%LOG%"
