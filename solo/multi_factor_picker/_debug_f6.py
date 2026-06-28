"""调试F6逻辑"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher
from trend_picker import get_daily_data

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = get_daily_data(fetcher, '600498.SH', '20260101', '20260611')

latest_pct = float(daily.iloc[-1]['pct_chg'])
latest_turnover = float(daily.iloc[-1].get('turnover_rate', 0) or 0)
is_limit_up = latest_pct >= 9.5

print(f'涨跌幅: {latest_pct:.1f}%')
print(f'换手率: {latest_turnover:.1f}%')
print(f'is_limit_up: {is_limit_up}')
print(f'\n判断: {"涨停" if is_limit_up else "非涨停"}')

# 手动测试逻辑
if is_limit_up:
    if latest_turnover >= 8:
        f6_score = 2.0
        note = '涨停启动日充分换手'
    elif latest_turnover >= 5:
        f6_score = 1.5
        note = '涨停启动日适中换手'
    else:
        f6_score = 1.0
        note = '缩量涨停（锁仓）'
else:
    if latest_turnover > 10:
        f6_score = 0.5
        note = '过热警告'
    else:
        f6_score = 1.0
        note = '换手适中'

print(f'\nF6得分: {f6_score}')
print(f'说明: {note}')
