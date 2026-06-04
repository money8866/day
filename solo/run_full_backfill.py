#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 禁用输出缓冲
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import theme_trend_sentiment_score as tts
from datetime import datetime, timedelta

print("=" * 80)
print("批量回溯最近 60 个交易日")
print("=" * 80)

# 步骤1: 获取交易日历
end_date = tts.TRADE_DATE
n_days = 60
start_cal_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=n_days * 2)).strftime("%Y%m%d")

cal = tts.pro.trade_cal(exchange='', start_date=start_cal_date, end_date=end_date)
cal = cal[cal['is_open'] == 1]
trade_dates = sorted(cal['cal_date'].tolist(), reverse=True)[:n_days]
trade_dates.reverse()  # 从旧到新处理

print(f"待处理的 {len(trade_dates)} 个交易日: {trade_dates[0]} 到 {trade_dates[-1]}")

# 步骤2: 只执行一次主题和成分股对应关系计算
print("\n[初始化] 计算主题和成分股对应关系（只需一次）")
hot_themes = tts.load_theme_json()
dc_df = tts.get_dc_members()
stock_basic = tts.get_stock_basic()
daily_basic = tts.get_daily_basic()
theme_stock_map, name_map_basic, stock_industry, stock_concepts = tts.match_theme_stocks(hot_themes, dc_df, stock_basic)

# 步骤3: 逐个日期处理
print(f"\n[开始处理] 共 {len(trade_dates)} 个交易日")
success_count = 0

for i, target_date in enumerate(trade_dates, 1):
    print(f"\n[{i}/{len(trade_dates)}] 处理 {target_date}")
    try:
        tts.main_for_date(target_date, hot_themes, dc_df, stock_basic, daily_basic, 
                        theme_stock_map, name_map_basic, stock_industry, stock_concepts)
        success_count += 1
    except Exception as e:
        print(f"处理 {target_date} 时出错: {e}")
        import traceback
        traceback.print_exc()

print(f"\n[全部完成] 成功处理 {success_count}/{len(trade_dates)} 个交易日")

# 检查最终结果
print("\n检查最终数据库...")
import sqlite3
conn = sqlite3.connect(tts.OUTPUT_DB)
cur = conn.cursor()
cur.execute('SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC')
dates = cur.fetchall()
print(f"数据库中共有 {len(dates)} 天的数据:")
for d in dates:
    print(f"  {d[0]}")
conn.close()
