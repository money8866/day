@echo off
chcp 65001 > nul
echo ========================================
echo 主线板块 + 中军分析系统 - 环境配置
echo ========================================
echo.

cd /d "%~dp0"

echo [1/3] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo 未找到 Python，请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo.

echo [2/3] 安装项目依赖...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo.
    echo 依赖安装失败，尝试使用官方源...
    pip install -r requirements.txt
)
echo.

echo [3/3] 检查配置文件...
if exist "..\TUSHARE.env" (
    echo 找到 TUSHARE.env 配置文件
) else (
    echo 警告: 未找到 TUSHARE.env 配置文件
)
echo.

echo ========================================
echo 环境配置完成！
echo ========================================
echo.
echo 现在可以运行:
echo   1. run_中军分析.bat  - 一键运行分析
echo   2. python main_with_backbone.py - 运行整合版
echo.
pause
