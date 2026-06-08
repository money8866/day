@echo off
chcp 65001 >nul
title 每日分析全流程

echo =============================================
echo  每日分析全流程 - %DATE% %TIME%
echo =============================================
echo.

set BASE=d:\mystock
cd /d %BASE%

echo [Step 1/5] 主题趋势分 + 情绪分...
echo =============================================
python solo\theme_trend_sentiment_score.py
echo.

echo [Step 2/5] 指数分析...
echo =============================================
python solo\market_analysis.py
echo.


echo [Step 3/5] ETF分析（引用主题）...
echo =============================================
python solo\etf_quant_theme.py
echo.

echo [Step 4/5] 个股形态选股...
echo =============================================
python solo\theme_pattern_stock_picker.py
echo.

echo [Step 5/5] 汇总输出...
echo =============================================
python solo\daily_analysis_summarizer.py
echo.

echo [Step 6/5] 汇总输出...
echo =============================================
python genindex.py
echo.

push.bat


echo =============================================
echo  全流程完成 - %DATE% %TIME%
echo =============================================

pause
