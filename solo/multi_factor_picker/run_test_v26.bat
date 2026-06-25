@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock\solo\multi_factor_picker
python wave2_pattern_scanner.py --pattern test --codes 300750.SZ 688981.SH 600519.SH --output csv
pause
