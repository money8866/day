import sys
sys.path.insert(0, '.')
import tushare as ts
import pandas as pd
import numpy as np

# 获取太辰光的K线数据
pro = ts.pro_api()
kline_df = pro.daily(ts_code='300570.SZ', start_date='20260301', end_date='20260611')
df_sorted = kline_df.sort_values('trade_date')

print('太辰光 (300570.SZ) 筛选条件检查:')
print('=' * 50)

# 1. 检查是否涨停
pct_chg_today = df_sorted.iloc[-1]['pct_chg']
print(f'1. 今日涨幅: {pct_chg_today:.2f}%')
print(f'   是否涨停(>=9.5%): {pct_chg_today >= 9.5}')

# 2. 检查市值
daily_basic = pro.daily_basic(ts_code='300570.SZ', trade_date='20260611')
mcap = daily_basic.iloc[0]['total_mv'] / 10000 if not daily_basic.empty else 0
print(f'\n2. 市值: {mcap:.2f}亿')
print(f'   是否在100-2000亿之间: {100 <= mcap <= 2000}')

# 3. 检查成交额
amounts = df_sorted['amount'].astype(float).values
recent_20 = df_sorted.iloc[-21:-1] if len(df_sorted) >= 21 else df_sorted
avg_amount_20 = recent_20['amount'].astype(float).mean() / 100000  # 转为亿
print(f'\n3. 近20日平均成交额: {avg_amount_20:.2f}亿')
print(f'   是否>=5亿: {avg_amount_20 >= 5}')

# 4. 检查涨幅
closes = df_sorted['close'].astype(float).values
close_today = closes[-1]

# 5日涨幅
if len(df_sorted) >= 6:
    close_5d_ago = df_sorted.iloc[-6]['close']
    pct_5d = (close_today - close_5d_ago) / close_5d_ago * 100
    print(f'\n4. 近5日涨幅: {pct_5d:.2f}%')
    print(f'   是否超过20%: {pct_5d > 20}')

# 10日涨幅
if len(df_sorted) >= 11:
    close_10d_ago = df_sorted.iloc[-11]['close']
    pct_10d = (close_today - close_10d_ago) / close_10d_ago * 100
    print(f'\n5. 近10日涨幅: {pct_10d:.2f}%')
    print(f'   是否超过50%: {pct_10d > 50}')

# 20日涨幅
if len(df_sorted) >= 21:
    close_20d_ago = df_sorted.iloc[-21]['close']
    pct_20d = (close_today - close_20d_ago) / close_20d_ago * 100
    print(f'\n6. 近20日涨幅: {pct_20d:.2f}%')
    print(f'   是否超过80%: {pct_20d > 80}')

# 7. 均线趋势检查
if len(closes) >= 25:
    ma5_vals = pd.Series(closes).rolling(5).mean().values
    ma10_vals = pd.Series(closes).rolling(10).mean().values
    ma20_vals = pd.Series(closes).rolling(20).mean().values
    
    close = closes[-1]
    ma5 = ma5_vals[-1]
    print(f'\n7. 均线趋势检查:')
    print(f'   收盘价: {close:.2f}')
    print(f'   MA5: {ma5:.2f}')
    print(f'   股价是否站上五日线: {close > ma5}')
    
    if len(ma10_vals) >= 5:
        ma10_slope = (ma10_vals[-1] - ma10_vals[-5]) / ma10_vals[-5] * 100
        print(f'\n   MA10斜率(近5日): {ma10_slope:.2f}%')
        print(f'   十日线是否向上: {ma10_slope > 0}')
    
    if len(ma20_vals) >= 5:
        ma20_slope = (ma20_vals[-1] - ma20_vals[-5]) / ma20_vals[-5] * 100
        print(f'\n   MA20斜率(近5日): {ma20_slope:.2f}%')
        print(f'   MA20是否持续向下(< -2%): {ma20_slope < -2}')