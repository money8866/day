@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set TUSHARE_TOKEN=
for /f "tokens=2 delims==" %%i in ('findstr /b "TUSHARE_TOKEN=" D:\mystock\config\.env') do set TUSHARE_TOKEN=%%i
cd /d D:\mystock\solo\multi_factor_picker
echo ============================================================
echo   二波形态精选v2.6 - 四形态并列扫描
echo ============================================================
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" -u wave2_pattern_scanner.py --pool gem_kc --pattern all --today --pdf --output output/wave2_all_patterns_%date:~5,2%%date:~8,2%.json
pause
