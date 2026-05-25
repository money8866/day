
@echo off
setlocal enabledelayedexpansion

set FOUND=0

for /f "tokens=2" %%i in ('wmic process where "name='python.exe'" get processid 2^&gt;nul ^| findstr /r "[0-9]"') do (
    wmic process where "processid=%%i" get commandline 2&gt;nul | find /I "main.py" &gt;nul
    if !ERRORLEVEL! EQU 0 (
        taskkill /F /PID %%i &gt;nul 2&gt;&amp;1
        set FOUND=1
    )
)

