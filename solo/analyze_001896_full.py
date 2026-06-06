import os
import sys
import pandas as pd
from datetime import datetime, timedelta
import tushare as ts
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
TS_TOKEN = os.getenv('TUSHARE_TOKEN', '')
pro = ts.pro_api(TS_TOKEN)

CACHE_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare')

target_date = "20260602"

# 1. 首先获取主题成分股数据
print("=" * 60)
print("步骤1: 加载主题配置和成分股")
print("=" * 60)

import theme_trend_sentiment_score as tss

hot_themes = tss.load_theme_json()
dc_df = tss.get_dc_members()
stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry,market,list_date')
theme_stock_map, name_map_basic, stock_industry, stock_concepts = tss.match_theme_stocks(hot_themes, dc_df, stock_basic)

code = "001896.SZ"
name = name_map_basic.get(code, code)
print(f"\n目标股票: {code} {name}")

print(f"\n检查主题成分股:")
for theme, stock_info in theme_stock_map.items():
    if code in stock_info:
        print(f"  ✓ {theme}")

print("\n" + "=" * 60)
print("步骤2: 获取股票基本面数据")
print("=" * 60)

daily_basic = pro.daily_basic(ts_code=code, trade_date=target_date)
if len(daily_basic) > 0:
    row = daily_basic.iloc[0]
    total_mv_yi = row['total_mv'] / 10000 if pd.notna(row['total_mv']) else 0  # 亿元
    print(f"  市值(亿): {total_mv_yi:.2f}")
    print(f"  收盘价: {row['close']:.2f}")
    
    # 检查市值条件
    if 200 <= total_mv_yi <= 2000:
        print(f"  ✓ 市值符合: 200-2000亿")
    else:
        print(f"  ✗ 市值不符合: {total_mv_yi:.2f}亿")

print("\n" + "=" * 60)
print("步骤3: 获取K线并检查当日涨跌")
print("=" * 60)

dt = datetime.strptime(target_date, "%Y%m%d")
start_date = (dt - timedelta(days=90)).strftime("%Y%m%d")
df = pro.daily(ts_code=code, start_date=start_date, end_date=target_date)

if len(df) > 0:
    df = df.sort_values('trade_date').reset_index(drop=True)
    last_row = df.iloc[-1]
    print(f"  {target_date}:")
    print(f"    收盘价: {last_row['close']}")
    print(f"    涨跌幅: {last_row['pct_chg']}%")
    
    if last_row['pct_chg'] >= 9.5:
        print(f"    ✗ 涨停 (≥9.5%)，排除")
    else:
        print(f"    ✓ 未涨停")
    
    print(f"\n最近20日涨停次数:")
    pct_changes = df['pct_chg'].astype(float).values
    zt_count_20 = sum(1 for p in pct_changes[-20:] if p >= 9.5)
    print(f"  近20日涨停数: {zt_count_20}")
    print(f"  注意: 该条件已从筛选中移除，仅供参考")

print("\n" + "=" * 60)
print("步骤4: 检查成交额条件")
print("=" * 60)

amounts = df['amount'].astype(float).values
if len(amounts) >= 21:
    avg_amount_20 = amounts[-21:-1].mean() / 100000  # 万元转亿元
    print(f"  近20日平均成交额: {avg_amount_20:.2f}亿")
    
    if avg_amount_20 >= 8:
        print(f"  ✓ 成交额≥8亿")
    else:
        print(f"  ✗ 成交额<8亿")

print("\n" + "=" * 60)
print("步骤5: 使用高级补涨检测器分析")
print("=" * 60)

from advanced_buzhang_analysis import AdvancedBuzhangDetector

detector = AdvancedBuzhangDetector()
result = detector.analyze_stock(df, None)

print(f"  综合评分: {result['overall_score']:.1f}")
print(f"  检测到形态: {result['detected_patterns']}")
print(f"  Valid: {result['valid']}")

if not result['valid']:
    print(f"  ✗ 评分 < 40，不入选")
else:
    print(f"  ✓ 评分 ≥ 40，入选")

print("\n详细形态分数:")
for pattern, score in result.get('pattern_scores', {}).items():
    print(f"  {pattern}: {score}")
