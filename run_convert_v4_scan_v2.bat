@echo off
chcp 65001 > nul
set PYTONIOENCODING=utf-8

python "D:\mystock\convert_v4_scan_to_pdf_v2.py" "D:\mystock\solo\report_daily\mainboard_v4_scan_20260618.json" "D:\mystock\solo\report_daily\mainboard_v4_scan_20260618_v2.pdf"

pause
