@echo off
REM ============================================================
REM   ELD V2 每日定时运行启动脚本
REM   用途：被 Windows 任务计划程序调用
REM   时间：每个交易日 17:00
REM ============================================================

setlocal
cd /d "D:\mystock\solo\eld"

REM 加载 Tushare Token
for /f "usebackq tokens=1,2 delims==" %%a in ("D:\mystock\config\.env") do (
    if "%%a"=="TUSHARE_TOKEN" (
        set "TUSHARE_TOKEN=%%b"
    )
)

REM 日志文件
set LOG_FILE=D:\mystock\solo\logs\eld_run_%date:~0,4%%date:~5,2%%date:~8,2%.log
if not exist "D:\mystock\solo\logs" mkdir "D:\mystock\solo\logs"

echo [%date% %time%] ELD V2 启动 >> "%LOG_FILE%"
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" "_run_eld.py" >> "%LOG_FILE%" 2>&1
echo [%date% %time%] ELD V2 结束, exit=%errorlevel% >> "%LOG_FILE%"

endlocal
