
@echo off
chcp 65001 &gt;nul

set SCRIPT_DIR=C:\Users\kongx\mystock\dayreal
set LOG_DIR=C:\Users\kongx\mystock\solo\logs
set MAIN_PY=%SCRIPT_DIR%\main.py

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set LOG_FILE=%LOG_DIR%\stock_monitor_%datetime:~0,4%%datetime:~4,2%%datetime:~6,2%_%datetime:~8,2%%datetime:~10,2%%datetime:~12,2%.log

cd /d "%SCRIPT_DIR%"

powershell -Command "&amp; { python '%MAIN_PY%' 2&gt;&amp;1 | Out-File -FilePath '%LOG_FILE%' -Encoding UTF8 }"

