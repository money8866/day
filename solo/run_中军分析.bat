@echo off
chcp 65001 > nul
echo ========================================
echo 主线板块 + 中军分析系统
echo ========================================
echo.

cd /d "%~dp0"

echo [1/2] 正在运行主线板块分析...
cd ..
python block.py
if errorlevel 1 (
    echo.
    echo 运行 block.py 失败，请检查 Python 环境和配置
    pause
    exit /b 1
)

echo.
echo [2/2] 正在运行中军分析...
cd solo
python main_with_backbone.py

echo.
echo ========================================
echo 分析完成！
echo ========================================
pause
