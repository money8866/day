@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

echo ======================================
echo 每日分析日志个股公告和资讯
echo ======================================
echo.

cd /d D:\mystock\solo

echo 开始分析...
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" "analyze_logged_stocks_30days.py"

echo.
echo 完成！
pause
