@echo off
title Realtime Theme Monitor
cd /d "d:\mystock\solo"
python realtime_theme_monitor.py >> "d:\mystock\solo\monitor_console.log" 2>&1
