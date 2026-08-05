@echo off
REM ============================================================
REM   ELD V2 Daily Run Script
REM   Called by Windows Task Scheduler at 17:00 every weekday
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
set LOG_FILE=D:\mystock\solo\logs\eld_run_%TODAY%.log
if not exist "D:\mystock\solo\logs" mkdir "D:\mystock\solo\logs"

echo [%date% %time%] ELD V2 Start >> "%LOG_FILE%"
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" "eld\_run_eld.py" >> "%LOG_FILE%" 2>&1
echo [%date% %time%] ELD V2 End, exit=%errorlevel% >> "%LOG_FILE%"

endlocal
