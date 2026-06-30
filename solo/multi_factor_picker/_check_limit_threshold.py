"""分析涨停实际数据"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 检查6月11日涨停股的实际涨幅
daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260611', end_date='20260611')
print(f'烽火通信6月11日：')
print(f'收盘价: {float(daily.iloc[0]["close"]):.2f}')
print(f'涨跌幅: {float(daily.iloc[0]["pct_chg"]):.2f}%')
print(f'涨跌额: {float(daily.iloc[0]["change"]):.2f}')

# 检查其他涨停股
daily2 = fetcher.pro.daily(ts_code='002409.SZ', start_date='20260610', end_date='20260610')
print(f'\n雅克科技6月10日：')
print(f'收盘价: {float(daily2.iloc[0]["close"]):.2f}')
print(f'涨跌幅: {float(daily2.iloc[0]["pct_chg"]):.2f}%')

# 检查st涨跌停判断
print(f'\n涨停阈值分析：')
print(f'普通股涨停：+10%（实际涨幅9.9-10.1%）')
print(f'ST股涨停：+5%')
print(f'北交所：+30%')
print(f'\n建议阈值：>=9.9%（包含四舍五入误差）')
