@echo off
chcp 65001 > nul
title 主线板块 + 中军分析系统
color 0A

echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║                                                            ║
echo ║          主线板块 + 中军分析系统                           ║
echo ║                                                            ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

echo [1/4] 正在检查环境...
python verify_env.py >nul 2>&1
if errorlevel 1 (
    echo.
    echo 环境未配置，正在自动配置...
    call setup_env.bat
    if errorlevel 1 (
        echo.
        echo 环境配置失败，请手动运行 setup_env.bat
        pause
        exit /b 1
    )
)

echo.
echo [2/4] 正在验证环境...
python verify_env.py
if errorlevel 1 (
    echo.
    echo 环境验证失败，请检查上述问题
    pause
    exit /b 1
)

echo.
echo [3/4] 正在运行主线板块分析...
cd ..
python block.py
if errorlevel 1 (
    echo.
    echo 主线板块分析失败
    pause
    exit /b 1
)

echo.
echo [4/4] 正在运行中军分析...
cd solo
python main_with_backbone.py

echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║                                                            ║
echo ║          分析完成！                                        ║
echo ║                                                            ║
echo ╚════════════════════════════════════════════════════════════╝
echo.
echo 结果文件保存在:
echo   ..\cache_backbone\main_backbone_analysis_*.csv
echo.
pause
