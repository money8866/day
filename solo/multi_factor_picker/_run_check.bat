@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock\solo\multi_factor_picker
python _check_tushare_date.py
