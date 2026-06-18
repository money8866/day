@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

python "D:\mystock\convert_json_to_pdf.py" "D:\mystock\solo\report_daily\mainboard_v2_scan.json" "D:\mystock\solo\report_daily\mainboard_v2_scan.pdf"

pause
