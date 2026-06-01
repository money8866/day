@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d d:\mystock\solo
"C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe" realtime_theme_monitor_clean.py > stdout_rt.log 2> stderr_rt.log

