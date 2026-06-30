"""直接测试修复后的F6逻辑（不导入模块）"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher
from trend_picker import get_daily_data

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = get_daily_data(fetcher, '600498.SH', '20260101', '20260611')

latest_pct = float(daily.iloc[-1]['pct_chg'])
latest_turnover = float(daily.iloc[-1].get('turnover_rate', 0) or 0)
is_limit_up = latest_pct >= 9.9

print(f'涨跌幅: {latest_pct:.1f}%')
print(f'换手率: {latest_turnover:.1f}%')
print(f'is_limit_up: {is_limit_up} (阈值9.9)\n')

# 直接应用修复后的逻辑
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
    elif 5 <= latest_turnover <= 10:
        f6_score = 1.0
        note = '换手适中'
    else:
        f6_score = 0.5
        note = '换手偏低'

print(f'F6得分: {f6_score}')
print(f'说明: {note}')
