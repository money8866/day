@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

echo =============================================
echo  运行 Tushare 量化分析
echo  开始时间: %DATE% %TIME%
echo =============================================
echo.

python tushare_quant.py >> stdout_tushare_quant.log 2>> stderr_tushare_quant.log

echo.
echo =============================================
echo  运行完成 - %DATE% %TIME%
echo  退出码: %ERRORLEVEL%
echo  日志: stdout_tushare_quant.log
echo =============================================
echo.
pause
