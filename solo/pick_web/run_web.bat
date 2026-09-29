@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Stock signal web (read-only)
rem Local:  run_web.bat
rem Cloud:  run_web.bat --host 0.0.0.0 --port 8000
rem The service opens picks_db\stock_picks.db READ-ONLY and never writes.
rem Reports tab reads Final_Self_<date>.html from the folder below.
cd /d %~dp0
python app.py --reports "d:\mystock\report_daily" %*
pause
