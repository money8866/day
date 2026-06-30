"""修复后的验证脚本"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 直接查询日线数据
daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260301', end_date='20260611')

print(f'获取到{len(daily)}条日线数据')
print(f'日期范围: {daily.iloc[-1]["trade_date"]} ~ {daily.iloc[0]["trade_date"]}')
print(f'最新收盘价: {float(daily.iloc[0]["close"]):.2f}')
print(f'最新涨跌幅: {float(daily.iloc[0]["pct_chg"]):.2f}%')

# 手动检查二波结构
print('\n【手动检查二波结构】')

# 找5月份的涨停日
may_data = daily[(daily['trade_date'] >= '20260501') & (daily['trade_date'] < '20260601')]
if len(may_data) > 0:
    may_limit_up = may_data[may_data['pct_chg'] >= 9.4]
    if len(may_limit_up) > 0:
        first_limit = may_limit_up.iloc[-1]
        print(f'首波涨停日: {first_limit["trade_date"]}')
        print(f'首波收盘价: {float(first_limit["close"]):.2f}')
        print(f'首波涨幅: {float(first_limit["pct_chg"]):.2f}%')

# 6月11日数据
jun11 = daily[daily['trade_date'] == '20260611'].iloc[0]
print(f'\n6月11日收盘价: {float(jun11["close"]):.2f}')
print(f'6月11日涨幅: {float(jun11["pct_chg"]):.2f}%')

# 检查是否突破首波
if float(jun11['close']) >= float(first_limit['close']) * 0.98:
    print('✓ 突破首波高点（98%阈值）')
else:
    print('✗ 未突破首波')
