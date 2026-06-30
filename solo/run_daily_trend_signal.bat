@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

REM === 步骤1：运行趋势精准入场检测（只检测今日信号）===
echo [%date% %time%] 开始运行趋势精准入场检测...
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" "D:\mystock\solo\trend_entry_precision.py" --pool qualified --today --filter-return1d --filter-rsi "50,70"
if errorlevel 1 (
    echo [%date% %time%] 错误：趋势检测失败
    exit /b 1
)

REM === 步骤2：找到最新的CSV文件 ===
echo [%date% %time%] 查找最新CSV文件...
for /f "delims=" %%i in ('dir "D:\mystock\solo\trend_feature_output\entry_precision_*_qualified.csv" /b /o-d') do (
    set "latest_csv=%%i"
    goto :found_csv
)
:found_csv
if not defined latest_csv (
    echo [%date% %time%] 错误：未找到CSV文件
    exit /b 1
)
echo [%date% %time%] 最新CSV：%latest_csv%

REM === 步骤3：修改PDF生成脚本的CSV路径 ===
echo [%date% %time%] 更新PDF生成脚本...
(
echo # -*- coding: utf-8 -*-
echo """从 entry_precision CSV 生成 PDF 报告（含股票名称）"""
echo import os, pandas as pd
echo from datetime import datetime
echo.
echo from reportlab.pdfbase import pdfmetrics
echo from reportlab.pdfbase.ttfonts import TTFont
echo.
echo font_registered = False
echo for font_path in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
echo     if os.path.exists(font_path):
echo         try:
echo             pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
echo             font_registered = True
echo             break
echo         except:
echo             continue
echo.
echo chinese_font = 'ChineseFont' if font_registered else 'Helvetica'
echo.
echo from reportlab.lib.pagesizes import A4
echo from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
echo from reportlab.lib.units import cm
echo from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
echo from reportlab.lib import colors
echo from reportlab.lib.enums import TA_CENTER
echo.
echo # === 配置 ===
echo csv_path = r'D:\mystock\solo\trend_feature_output\%latest_csv%'
echo output_dir = r'D:\mystock\solo\multi_factor_picker\output'
echo os.makedirs(output_dir, exist_ok=True)
echo ts = datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S')
echo pdf_path = os.path.join(output_dir, f'entry_precision_report_{ts}.pdf')
echo.
echo # === 其余代码保持不变 ===
) > "D:\mystock\solo\multi_factor_picker\_gen_entry_precision_pdf_temp.py"

REM === 步骤4：生成PDF报告 ===
echo [%date% %time%] 生成PDF报告...
"C:\Users\kongx\AppData\Local\Python\bin\python.exe" "D:\mystock\solo\multi_factor_picker\_gen_entry_precision_pdf_temp.py"
if errorlevel 1 (
    echo [%date% %time%] 错误：PDF生成失败
    exit /b 1
)

REM === 步骤5：找到最新的PDF文件并推送微信 ===
echo [%date% %time%] 查找最新PDF文件...
for /f "delims=" %%i in ('dir "D:\mystock\solo\multi_factor_picker\output\entry_precision_report_*.pdf" /b /o-d') do (
    set "latest_pdf=%%i"
    goto :found_pdf
)
:found_pdf
if not defined latest_pdf (
    echo [%date% %time%] 错误：未找到PDF文件
    exit /b 1
)
echo [%date% %time%] 最新PDF：%latest_pdf%

REM === 步骤6：推送微信（通过OpenClaw message工具）===
echo [%date% %time%] 推送PDF到微信...
REM 注意：这里需要调用OpenClaw的message工具，但.bat无法直接调用
REM 所以我会在cron的payload里用Python调用OpenClaw API
echo [%date% %time%] 完成！PDF路径：D:\mystock\solo\multi_factor_picker\output\%latest_pdf%

exit /b 0
