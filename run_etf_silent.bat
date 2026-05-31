@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock
"C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe" etf_quant.py > stdout_etf.log 2> stderr_etf.log
echo ETF_EXIT_CODE=%ERRORLEVEL% >> stdout_etf.log
echo Run time: %DATE% %TIME% >> stdout_etf.log