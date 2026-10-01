@echo off
REM ============================================================
REM   HVE V1 Daily Run Script（可选挂 Windows Task Scheduler）
REM   用法：run_hve_daily.bat [YYYYMMDD]
REM   缺省日期 = 最近有效交易日（stock_cache.get_effective_date）
REM   说明：本脚本不会被自动注册；是否定时由用户自行决定（B 方案）
REM   前置：需在 run_all 之后运行（依赖 stock_data.db 当日缓存已更新）
REM ============================================================

setlocal
cd /d "D:\mystock\solo"

REM Load Tushare Token
for /f "usebackq tokens=1,2 delims==" %%a in ("D:\mystock\config\.env") do (
    if "%%a"=="TUSHARE_TOKEN" (
        set "TUSHARE_TOKEN=%%b"
    )
)

REM Log file (use PowerShell for reliable date format)
for /f %%i in ('powershell -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
set LOG_FILE=D:\mystock\solo\logs\hve_run_%TODAY%.log
if not exist "D:\mystock\solo\logs" mkdir "D:\mystock\solo\logs"

set ARGS=
if not "%~1"=="" set ARGS=--date %~1

echo [%date% %time%] HVE V1 Start %ARGS% >> "%LOG_FILE%"
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" "hve_v1\run_daily.py" %ARGS% --push >> "%LOG_FILE%" 2>&1
echo [%date% %time%] HVE V1 End, exit=%errorlevel% >> "%LOG_FILE%"

endlocal
