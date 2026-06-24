@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set TUSHARE_TOKEN=1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34
cd /d D:\mystock
echo ============================================================
echo   二波形态精选 v2.0 - stk_factor_pro 多指标共振版
echo ============================================================
py -u solo\multi_factor_picker\wave2_pattern_scanner.py %*
pause
