@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set TUSHARE_TOKEN=1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34
cd /d D:\mystock\solo\multi_factor_picker
echo ============================================================
echo   二波形态精选v2.6 - 四形态并列扫描
echo ============================================================
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" -u wave2_pattern_scanner.py --pool gem_kc --pattern all --today --pdf --output output/wave2_all_patterns_%date:~5,2%%date:~8,2%.json
pause
