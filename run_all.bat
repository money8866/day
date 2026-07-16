@echo off
chcp 65001 >nul
title 每日分析全流程

echo =============================================
echo  每日分析全流程 - %DATE% %TIME%
echo =============================================
echo.

set BASE=d:\mystock
cd /d %BASE%

REM python solo\build_theme_stock_map.py 

echo [Step 1/7] 主题趋势分 + 情绪分...
echo =============================================
python solo\theme_trend_sentiment_score.py
echo.
python D:\mystock\solo\theme_alpha_v6\main.py

echo [Step 2/7] 指数分析...
echo =============================================
python solo\market_analysis.py
echo.


echo [Step 3/7] ETF分析（引用主题）...
echo =============================================
rem python solo\etf_quant_theme.py
echo.
python solo\etf_mainline_strategy_tushare.py

echo [Step 4/7] 个股形态选股...
echo =============================================
rem python solo\theme_pattern_stock_picker.py
echo.


echo 先生成趋势信号

rem python solo\multi_factor_picker\wave2_pattern_scanner.py --csv D:\mystock\solo\report_daily\bull_stocks_qualified.csv --output csv --pdf --today

rem python solo\bwave_strategy.py

rem python solo\etf_resonance\wave3_detector.py --scan --scope etf --top 20
rem python solo\etf_resonance/rebound_detector.py --scope etf --min-score 60 --top 20

echo [Step 5/7] 自选量化

echo =============================================
echo [Step 5.0] ETF补涨扩散策略 (生成catchup_signals.csv)...
python solo\etf_resonance\run_real.py


echo =============================================
echo [Step 5.0] ETF Alpha策略 ...
python solo\etf_resonance\run_real.py
echo =============================================

python solo\etf_alpha_engine\main.py
python solo\etf_alpha_engine\report_sender.py


echo =============================================
python solo\tushare_quant.py
echo.

echo [Step 6/7] 汇总输出...
echo =============================================
python solo\daily_analysis_summarizer.py
echo.

echo [Step 7/7] 主题成份生成...
echo =============================================
rem python solo\theme_portfolio_strategy_cached_dc.py

echo [Step 8/7] 生成index.html  
echo =============================================
python genindex.py
echo.

push.bat


echo =============================================
echo  全流程完成 - %DATE% %TIME%
echo =============================================

pause
