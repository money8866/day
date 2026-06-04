@echo off
chcp 65001 >nul
title 完整量化分析系统
color 0A

cd /d "%~dp0"

:: 日志文件设置
set LOGDIR=%~dp0logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOGFILE=%LOGDIR%\run_all_%date:~0,4%%date:~5,2%%date:~8,2%.log
if exist "%LOGFILE%" del "%LOGFILE%"

echo. > "%LOGFILE%"

echo ============================================= >> "%LOGFILE%"
echo  完整量化分析系统 - %DATE% %TIME% >> "%LOGFILE%"
echo ============================================= >> "%LOGFILE%"
echo. >> "%LOGFILE%"

:: 6步流程，输出同时打印和写日志
echo [1/6] 正在运行主题趋势情绪评分...
echo [1/6] 正在运行主题趋势情绪评分... >> "%LOGFILE%"
python theme_trend_sentiment_score.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo 主题趋势情绪评分失败 >> "%LOGFILE%"
    exit 1
)
echo.

echo [2/6] 正在运行大盘分析与仓位建议...
echo [2/6] 正在运行大盘分析与仓位建议... >> "%LOGFILE%"
python market_analysis.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo 大盘分析失败 >> "%LOGFILE%"
    exit 1
)
echo.

echo [3/6] 正在运行主题选股...
echo [3/6] 正在运行主题选股... >> "%LOGFILE%"
python theme_pattern_stock_picker.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo 主题选股失败 >> "%LOGFILE%"
    exit 1
)
echo.

echo [4/6] 正在运行ETF量化主题分析...
echo [4/6] 正在运行ETF量化主题分析... >> "%LOGFILE%"
python etf_quant_theme.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo ETF量化主题分析失败 >> "%LOGFILE%"
    exit 1
)
echo.

echo [5/6] 正在运行Tushare量化分析...
echo [5/6] 正在运行Tushare量化分析... >> "%LOGFILE%"
python tushare_quant.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo Tushare量化分析失败 >> "%LOGFILE%"
    exit 1
)
echo.

echo [6/6] 正在运行每日分析总结器...
echo [6/6] 正在运行每日分析总结器... >> "%LOGFILE%"
python daily_analysis_summarizer.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo 每日分析总结器失败 >> "%LOGFILE%"
    exit 1
)
echo.

echo ============================================= >> "%LOGFILE%"
echo  全流程完成 - %DATE% %TIME% >> "%LOGFILE%"
echo ============================================= >> "%LOGFILE%"
exit 0
