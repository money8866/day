"""检查烽火通信完整走势（判断一波vs二波）"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260401', end_date='20260611')
daily = daily.sort_values('trade_date')

print('=== 烽火通信4-6月走势（判断一波二波）===')
print(f'{"日期":<12s} | {"收盘":<8s} | {"涨跌":<8s} | {"换手":<8s}')
print('-' * 50)

wave_dates = []
for _, row in daily.iterrows():
    date = row['trade_date']
    close = row['close']
    pct = row['pct_chg']
    turnover = row.get('turnover_rate', 0) or 0
    
    mark = ''
    if pct >= 9.5:
        mark = '***涨停'
        wave_dates.append(str(date))
    elif pct >= 5:
        mark = '**大涨'
    elif pct <= -5:
        mark = '**大跌'
    
    print(f'{date} | {close:>6.2f} | {pct:>+6.1f}% | {turnover:>5.1f}% {mark}')

print(f'\n=== 涨停日期统计 ===')
print(f'涨停次数: {len(wave_dates)}次')
print(f'涨停日期: {", ".join(wave_dates)}')

# 判断一波二波
if len(wave_dates) >= 2:
    print(f'\n分析:')
    print(f'- 首波涨停: {wave_dates[0]}（一波启动）')
    print(f'- 二波涨停: {wave_dates[-1]}（二波确认）')
    print(f'- 6月11日属于: {"二波确认" if "20260611" in wave_dates[-1] else "一波启动"}')
else:
    print(f'\n分析: 6月11日为一波启动日')
