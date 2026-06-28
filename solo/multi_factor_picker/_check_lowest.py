"""验证回踩最低点数据"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 获取5月8日后的所有日线
daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260508', end_date='20260611')
print(f'5月8日后数据: {len(daily)}条')
print(f'日期范围: {daily.iloc[-1]["trade_date"]} ~ {daily.iloc[0]["trade_date"]}')

# 找最低价
lowest = daily['low'].min()
lowest_date = daily.loc[daily['low'].idxmin(), 'trade_date']
print(f'\n最低价: {float(lowest):.2f}')
print(f'日期: {lowest_date}')

# 显示6月前几天的数据
jun_data = daily[daily['trade_date'].between('20260601', '20260611')]
print(f'\n6月数据:')
print(jun_data[['trade_date', 'open', 'high', 'low', 'close', 'pct_chg']].to_string(index=False))
