@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock\solo\multi_factor_picker
echo [%DATE% %TIME%] BullScore 主管线启动...
C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe -u main.py > run_bullscore_main_log.txt 2>&1
echo [%DATE% %TIME%] BullScore 主管线完成，退出码 %ERRORLEVEL%
pause
