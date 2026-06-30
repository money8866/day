"""详细调试二波检测"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 获取数据
daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260301', end_date='20260611')
basic = fetcher.pro.daily_basic(ts_code='600498.SH', start_date='20260301', end_date='20260611')

daily_merged = daily.merge(basic[['trade_date', 'turnover_rate']], on='trade_date', how='left')

print(f'总数据: {len(daily_merged)}')
print(f'日期范围: {daily_merged.iloc[-1]["trade_date"]} ~ {daily_merged.iloc[0]["trade_date"]}')
print(f'首条: {daily_merged.iloc[0]["trade_date"]} 涨幅{float(daily_merged.iloc[0]["pct_chg"]):.1f}%')
print(f'末条: {daily_merged.iloc[-1]["trade_date"]} 涨幅{float(daily_merged.iloc[-1]["pct_chg"]):.1f}%')

# 手动执行二波检测逻辑
lookback_days = 60

# Step 1: 取最近60天（排除最近5天）
recent = daily_merged.head(lookback_days).iloc[5:]  # 数据倒序
print(f'\n最近60天（排除最近5天）: {len(recent)}条')
print(f'日期范围: {recent.iloc[-1]["trade_date"]} ~ {recent.iloc[0]["trade_date"]}')

if len(recent) == 0:
    print('❌ 没有数据！')
else:
    # Step 2: 找首波涨停日
    limit_up_days = recent[recent['pct_chg'] >= 9.4]
    print(f'\n涨停日数量: {len(limit_up_days)}')

    if len(limit_up_days) > 0:
        wave1_idx = limit_up_days['pct_chg'].idxmax()
        wave1_row = limit_up_days.loc[wave1_idx]
        print(f'✓ 找到首波涨停日')
        print(f'  日期: {wave1_row["trade_date"]}')
        print(f'  涨幅: {float(wave1_row["pct_chg"]):.2f}%')
        print(f'  收盘: {float(wave1_row["close"]):.2f}')
        print(f'  索引: {wave1_idx}')
    else:
        # 找最大涨幅日
        wave1_idx = recent['pct_chg'].idxmax()
        wave1_row = recent.loc[wave1_idx]
        print(f'✓ 找到首波最大涨幅日（非涨停）')
        print(f'  日期: {wave1_row["trade_date"]}')
        print(f'  涨幅: {float(wave1_row["pct_chg"]):.2f}%')
