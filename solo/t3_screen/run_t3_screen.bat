@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
rem T+3 execution screen (standalone entry).
rem Usage: run_t3_screen.bat [--date YYYYMMDD] [--live] [--print] [--validate]
cd /d %~dp0
python t3_screen_build.py %*
if errorlevel 1 pause
