@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

python "D:\mystock\convert_md_to_pdf.py" "D:\mystock\solo\report_daily\s_stock_deep_analysis.md" "D:\mystock\solo\report_daily\s_stock_deep_analysis.pdf"

pause
