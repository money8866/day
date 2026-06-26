@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock\solo\multi_factor_picker
echo [%DATE% %TIME%] 创新高后回落回测启动...
C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe -u new_high_pullback_backtest.py > new_high_pullback_log.txt 2>&1
echo [%DATE% %TIME%] 回测完成，退出码 %ERRORLEVEL%
pause
