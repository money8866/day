@echo off
chcp 65001 > nul
cd /d D:\mystock
"C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe" tushare_quant.py > stdout.log 2> stderr.log
echo EXIT_CODE=%ERRORLEVEL% >> stdout.log
echo 完成时间: %DATE% %TIME% >> stdout.log
pause
