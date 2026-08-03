@echo off
chcp 65001 >nul
cd /d D:\mystock\solo\multi_factor_picker
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set LOGDATE=%%i
set LOG=%TEMP%\washout_push_%LOGDATE%.log

echo [%date% %time%] 开始生成洗盘修复报告... > %LOG%
python enhanced_timing_bull_all.py >> %LOG% 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] 报告生成失败，错误码=%errorlevel% >> %LOG%
    exit /b %errorlevel%
)

echo [%date% %time%] 开始推送微信... >> %LOG%
python push_washout_recovery.py >> %LOG% 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] 微信推送失败，错误码=%errorlevel% >> %LOG%
    exit /b %errorlevel%
)

echo [%date% %time%] 全部完成！>> %LOG%
