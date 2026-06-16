@echo off
echo ========================================
echo    Real-time Monitor Task Setup
echo ========================================
echo.

echo Setting up tasks...

:: Delete old tasks
schtasks /delete /tn "Real-time Emotion Monitor" /f >nul 2>&1
schtasks /delete /tn "Real-time MA Monitor" /f >nul 2>&1

:: Create emotion monitor task
echo.
echo [1/2] Creating Real-time Emotion Monitor...
schtasks /create /tn "Real-time Emotion Monitor" /tr "d:\mystock\solo\start_emotion_monitor.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:25 /rl highest /f
if %errorlevel% equ 0 (
    echo   [OK] Success
) else (
    echo   [FAIL] Failed
)

:: Create MA monitor task
echo.
echo [2/2] Creating Real-time MA Monitor...
schtasks /create /tn "Real-time MA Monitor" /tr "d:\mystock\solo\start_ma_monitor.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:25 /rl highest /f
if %errorlevel% equ 0 (
    echo   [OK] Success
) else (
    echo   [FAIL] Failed
)

echo.
echo ========================================
echo Setup Complete!
echo ========================================
echo.
echo To verify:
echo   - Press Win+R, type taskschd.msc
echo   - Find tasks: Real-time Emotion Monitor, Real-time MA Monitor
echo.
pause
