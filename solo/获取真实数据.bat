@echo off
chcp 65001 >nul
echo ================================================
echo   2026年5月22日 真实数据抓取 + 中军分析
echo ================================================
echo.

cd /d "%~dp0"

echo [步骤1] 检查Python环境...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo   未找到Python，请先安装Python 3.8+
    echo   下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

python --version
echo.

echo [步骤2] 检查/安装依赖...
python -c "import tushare" >nul 2>&1
if %errorlevel% neq 0 (
    echo   正在安装 tushare...
    pip install tushare pandas numpy python-dotenv -q
)

echo.

echo [步骤3] 获取5月22日真实数据...
echo.

python get_real_data.py

echo.
echo ================================================
echo   按任意键退出...
echo ================================================
pause >nul
