"""查看雅克科技5-6月走势"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = fetcher.pro.daily(ts_code='002409.SZ', start_date='20260501', end_date='20260610')
daily = daily.sort_values('trade_date')

print('=== 雅克科技5-6月走势 ===')
for _, row in daily.iterrows():
    pct = row['pct_chg']
    close = row['close']
    vol = row['vol'] / 10000
    date = row['trade_date']
    mark = '***' if abs(pct) > 7 else ''
    print(f'{date} | {close:.2f} | {pct:+5.1f}% | {vol:6.0f}万手 {mark}')
