@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d D:\mystock\solo\multi_factor_picker
py wave2_pattern_scanner.py --csv output/bull_stocks.csv --output json --pdf --today > output\scan_log.txt 2>&1
echo SCAN COMPLETE >> output\scan_log.txt
