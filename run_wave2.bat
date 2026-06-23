@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock
py -u solo\multi_factor_picker\wave2_backtest.py
pause
