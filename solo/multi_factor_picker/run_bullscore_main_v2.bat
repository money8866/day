@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe -u D:\mystock\solo\multi_factor_picker\main.py > D:\mystock\solo\multi_factor_picker\run_main_out.log 2> D:\mystock\solo\multi_factor_picker\run_main_err.log
echo DONE %DATE% %TIME% >> D:\mystock\solo\multi_factor_picker\run_main_out.log
pause
