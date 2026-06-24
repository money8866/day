@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock\solo\multi_factor_picker
py wave2_cy_full_backtest_v3.py > run_cy_v3_log.txt 2>&1
 echo [%TIME%] 回测完成，退出码 %ERRORLEVEL%
