@echo off
setlocal enabledelayedexpansion

set FOUND=0

for /f "tokens=2" %%i in ('wmic process where "name='python.exe'" get processid 2^>nul ^| findstr /r "[0-9]"') do (
    wmic process where "processid=%%i" get commandline 2>nul | find /I "realtime_theme_monitor.py" >nul
    if !ERRORLEVEL! EQU 0 (
        taskkill /F /PID %%i >nul 2>&1
        set FOUND=1
    )
)

if %FOUND% EQU 1 (
    echo Realtime Theme Monitor stopped.
) else (
    echo No running Realtime Theme Monitor found.
)
