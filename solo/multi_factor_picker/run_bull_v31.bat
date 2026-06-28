@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo BullScore v3.1 优化版主管线启动
echo 启动时间: %date% %time%

cd /d D:\mystock\solo\multi_factor_picker

set PYTHONPATH=D:\mystock\solo\multi_factor_picker;D:\mystock

"C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe" main.py --mode scan --batch 50

echo.
echo 扫描完成，按任意键退出...
pause >nul
