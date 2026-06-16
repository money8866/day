@echo off
setlocal enabledelayedexpansion
title Stock Monitor - Stop Script

echo ========================================================
echo Stopping Stock Monitor...
echo Stop Time: %date% %time%
echo ========================================================
echo.

set FOUND=0

for /f "tokens=2" %%i in ('wmic process where "name='python.exe'" get processid 2^>nul ^| findstr /r "[0-9]"') do (
    wmic process where "processid=%%i" get commandline 2>nul | find /I "main.py" >nul
    if !ERRORLEVEL! EQU 0 (
        echo Found running main.py process (PID: %%i)
        taskkill /F /PID %%i >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            echo Successfully terminated process %%i
            set FOUND=1
        ) else (
            echo Failed to terminate process %%i
        )
    )
)

if !FOUND! EQU 0 (
    echo No running main.py process found
)

echo.
echo Operation completed
pause

