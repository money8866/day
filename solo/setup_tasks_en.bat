@echo off
title Stock Monitor - Task Scheduler Setup

echo ========================================================
echo Stock Monitor - Task Scheduler Setup
echo ========================================================
echo.
echo This script will create Windows Task Scheduler tasks
echo Administrator rights required
echo.
echo Checking permissions...

net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [Error] Administrator rights required!
    echo Please right-click and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo [OK] Administrator rights obtained
echo.

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File setup_tasks_en.ps1

echo.
pause

