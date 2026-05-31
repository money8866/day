@echo off
chcp 65001 > nul
echo ========================================
echo 每日涨停跟踪与复盘系统
echo ========================================
echo.

:: 设置日期（可以修改为特定日期）
set TRADE_DATE=%date:~0,4%%date:~5,2%%date:~8,2%

:: 如果传入了参数，使用参数作为日期
if not "%1"=="" set TRADE_DATE=%1

echo 交易日期: %TRADE_DATE%
echo.

:: 运行涨停跟踪程序
python "%~dp0limit_track_review.py" %TRADE_DATE%

echo.
echo ========================================
echo 程序执行完成
echo ========================================
pause
