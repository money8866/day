@echo off
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo ======================================
echo   复盘PDF生成器
echo ========================================
python "%~dp0_final_pdf.py" %*
echo.
echo 按任意键退出...
pause >nul
