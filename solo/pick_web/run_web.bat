@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Stock signal web (read-only)
rem Local:  run_web.bat
rem Cloud:  run_web.bat --host 0.0.0.0 --port 8000
rem The service opens picks_db\stock_picks.db READ-ONLY and never writes.
cd /d %~dp0
python app.py %*
pause
