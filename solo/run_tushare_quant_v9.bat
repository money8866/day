@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock\solo
"C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe" tushare_quant.py > stdout_tq_solo.log 2> stderr_tq_solo.log
echo EXIT_CODE=%ERRORLEVEL% >> stdout_tq_solo.log
echo DONE_TIME: %DATE% %TIME% >> stdout_tq_solo.log
