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
python solo\build_theme_stock_map_v2.py
python solo\theme_score_v2.py

REM 主题热度 V2.4：下游 market_analysis(Step2) 与 tushare_quant(Step5) 的热度唯一来源，
REM 必须先于它们生成；漏跑会让下游静默回退 theme_scores.db
python solo\theme_heat_v22.py

rem python d:\mystock\solo\multi_factor_picker\main.py

REM python solo\theme_trend_sentiment_score.py
echo.
REM python D:\mystock\solo\theme_alpha_v6\main.py
REM python d:\mystock\solo\theme_forecast\main.py

echo [Step 2/7] 指数分析...
echo =============================================
python solo\market_analysis.py
echo.

echo [Step 2.5/7] 中证2000ETF仓位管理...
echo =============================================
python -m solo.position_gate.daily --save-plan
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

rem python solo\multi_factor_picker\wave2_pattern_scanner.py --csv D:\mystock\solo\report_daily\bull_stocks.csv --output csv --pdf --today

rem python solo\bwave_strategy.py

rem python solo\etf_resonance\wave3_detector.py --scan --scope etf --top 20
rem python solo\etf_resonance/rebound_detector.py --scope etf --min-score 60 --top 20

echo [Step 5.1/7] 自选量化

echo =============================================
echo [Step 5.2/7] ETF补涨扩散策略 (生成catchup_signals.csv)...
rem python solo\etf_resonance\run_real.py
rem python solo\scan_etf_alpha_v5.py

echo [Step 5.3/7] 中报预增股池择时（回踩算法）

echo =============================================
rem python -X utf8 solo\zhongbao_hunter.py --backfill

rem python d:\mystock\solo\multi_factor_picker\enhanced_timing_bull_all.py

python solo\volume_surge_select.py

echo =============================================
python solo\tushare_quant.py

echo [Step 5.25/7] W7 二波新口径信号 (T0锚定·类型A缩量回踩止跌 / 类型B回踩突破)...
echo =============================================
REM 依赖 daily_cache（tushare_quant 已写入当日行情），故必须排在其后。
REM 引擎自身会落 stock_pick_db(strategy=w7_ab, replace=True)，信号随报告同步入库。
REM 全市场扫描较慢（约十几分钟），失败不阻塞后续步骤。
python solo\w7_second_wave_engine.py
echo.

echo [Step 5.3/7] W7 逆势缩量回踩低吸 (BREAKOUT_RETEST优化版·独立信号池)...
echo =============================================
REM 20261028 新增：BREAKOUT_RETEST 优化版（量比<0.60 + 大盘20日<0 + 回踩≥8根 + 结构止损）。
REM 标定结论已被同日的前瞻回测修正（见 solo/report_daily/w7_retest_validation_20261028.md）：
REM   原声称 T+3 +4.89%/胜64.7%（n=17）系小样本过拟合，已证伪；
REM   独立前瞻回测（66 个逆势日 / 257 条）实测 T+3 +0.34%/超+0.30%，T+10 -1.46%。
REM   加入「回踩≥8根」下限（突破后必须真回踩完）后：T+3 +1.02%/超+1.01%，T+5 +1.35%/超+1.43%。
REM   仍属观察级：分月不稳（202607 -2.44%、202609 +3.22%、202610 -3.77%），不可当成熟策略实盘。
REM 独立落 stock_pick_db(strategy=w7_retest)，不改 w7_ab 主链；样本靠每日跑批自然积累，
REM 攒够 30 条后用 solo/_w7_ab_review.py 同套口径复检，再决定是否并入主链。
REM 依赖 daily_cache（tushare_quant 已写入当日行情），故排在其后；失败不阻塞后续步骤。
REM 日期取 daily_cache 最新交易日（run_all.bat 不接日期参数）
for /f "usebackq delims=" %%d in (`python -c "import sqlite3;print(sqlite3.connect(r'd:\mystock\cache_daily\stock_data.db').execute('select max(trade_date) from daily_cache').fetchone()[0])"`) do set W7DATE=%%d
echo [w7_retest] 使用交易日 %W7DATE%
python solo\w7_retest_signal.py --date %W7DATE%
echo.

echo [Step 5.32/7] RB 底部抬高（中长线形态 + 主题共振）...
echo =============================================
REM 20261028 新增：中长下跌见底后回踩低点逐级抬高（≥4 次、累计 ≥20%）、MA20 上行、
REM 短均线站上 MA60，买点落在抬高后的回踩位，持有 20~60 个交易日。
REM 主题联动：今日命中池内同题材家数越多，综合分越高（技术分 + 0~15 主题分）。
REM 依赖 daily_cache（tushare_quant 已写入当日行情），排在其后；失败不阻塞后续步骤。
REM 脚本自身会落库(stock_pick_db strategy=rb_trend, replace=True)，并单独发一封邮件日报。
python solo\rising_bottom_select.py
echo.

echo [Step 5.35/7] 入场信号记分卡（追加进 Final_Self，含与昨日对比）...
echo =============================================
REM 只做记分卡追加（--skip-gen 不重跑 tushare_quant、不重复发邮件；
REM --allow-stale-date 让周末补跑时也执行）。必须排在 tushare_quant 之后、
REM genindex/push 之前，否则 index.html 与推送内容不含记分卡。
python _daily_review.py --skip-gen --allow-stale-date
echo.

echo [Step 5.4/7] 三级行业共振选股（当日涨停 ≥2 家的三级行业）...
echo =============================================
REM 依赖 daily_cache（上一步 tushare_quant 已写入当日行情），故必须排在此处
python -X utf8 solo\l3_resonance_scanner.py
echo.
echo [Step 6/7] 汇总输出...
echo =============================================
rem python solo\daily_analysis_summarizer.py
echo.

echo [Step 7/7] 主题成份生成...
echo =============================================
rem python solo\theme_portfolio_strategy_cached_dc.py

echo [Step 8/7] 生成index.html  
echo =============================================
python genindex.py
echo.

call push.bat


echo =============================================
echo [Step 8.5/7] 选股库跟踪收益回填 (T+1/3/5/10/20)...
echo =============================================
REM 回填 pick_tracking（收益/胜率/最大回撤/止损命中），须在 sync_db 之前，
REM 否则同步到云端的是缺 T+N 的半成品。新口径 w7_ab 与其它策略一并回填。
python solo\stock_pick_db.py tracking
echo.

echo [Step 9/7] 选股库 + 复盘报告同步到云服务器...
echo =============================================
python solo\pick_web\sync_db.py






echo =============================================
echo  全流程完成 - %DATE% %TIME%
echo =============================================

pause
