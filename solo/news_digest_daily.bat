@echo off
REM ============================================================
REM   每日新闻 AI 总结（邮件日报）
REM   Windows 任务计划程序 每天 08:00 调用
REM   总结「前一日」资讯 + 同花顺日榜题材 → DeepSeek → HTML 邮件
REM   推送 stock1975@qq.com
REM ============================================================
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
setlocal
cd /d "%~dp0"

set LOG_DIR=D:\mystock\solo\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f %%i in ('powershell -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
set LOG_FILE=%LOG_DIR%\news_digest_%TODAY%.log

echo ========== [%date% %time%] news digest start ========== >> "%LOG_FILE%"
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" "daily_news_digest.py" >> "%LOG_FILE%" 2>&1
echo ========== [%date% %time%] news digest end, exit=%errorlevel% ========== >> "%LOG_FILE%"

endlocal
