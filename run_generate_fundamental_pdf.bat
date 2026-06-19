@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

python "D:\mystock\generate_fundamental_pdf.py"

pause
