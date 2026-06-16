@echo off
chcp 65001 >nul
title 2026年5月22日 主线板块+中军分析
color 0A

echo.
echo  ==================================================
echo     2026年5月22日 主线板块 + 中军分析（真实数据）
echo  ==================================================
echo.

cd /d "%~dp0"

set PYTHON=C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe

echo [1/4] 检查Python环境...
"%PYTHON%" --version
if %errorlevel% neq 0 (
    echo.
    echo [错误] 未找到Python：%PYTHON%
    echo.
    pause
    exit /b 1
)
echo.

echo [2/4] 检查依赖包...
"%PYTHON%" -c "import tushare, pandas, numpy" 2>nul
if %errorlevel% neq 0 (
    echo   正在安装所需依赖...
    "%PYTHON%" -m pip install tushare pandas numpy python-dotenv -q
    echo   安装完成
)
echo.

echo [3/4] 获取真实数据并分析...
echo.
"%PYTHON%" get_real_data.py

echo.
echo  ==================================================
echo     分析完成！按任意键退出...
echo  ==================================================
pause >nul
