@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python -m py_compile D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py
if %errorlevel% equ 0 (
    echo Syntax OK
) else (
    echo Syntax ERROR
)
