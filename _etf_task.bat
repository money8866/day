@echo off
chcp 65001 > nul
set TUSHARE_TOKEN=1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34
set PYTHONIOENCODING=utf-8
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" D:\mystock\etf_quant.py >> D:\mystock\stdout_etf_v51.log 2>&1
