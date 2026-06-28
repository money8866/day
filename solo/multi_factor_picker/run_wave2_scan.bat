@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set TUSHARE_TOKEN=1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34

echo ================================================================================
echo V型急跌评分优化v2.11扫描
echo ================================================================================
echo.

cd /d D:\mystock\solo\multi_factor_picker

echo 开始扫描双创板股票池...
echo.

C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe wave2_pattern_scanner.py --pattern all --pool gem_kc --pdf --today

echo.
echo ================================================================================
echo 扫描完成！
echo ================================================================================

pause
