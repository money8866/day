"""查看烽火通信6月完整走势"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher

for _l in open(r'D:\mystock\config\.env'):
    if _l.strip().startswith('TUSHARE_TOKEN='):
        token = _l.strip().split('=', 1)[1].strip().strip('"')
        break
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = fetcher.pro.daily(ts_code='600498.SH', start_date='20260601', end_date='20260626')
daily = daily.sort_values('trade_date')

print('=== 烽火通信6月走势 ===')
print(f'{"日期":<12s} | {"收盘":<8s} | {"涨跌":<8s} | {"换手":<8s} | {"成交量":<10s}')
print('-' * 60)
for _, row in daily.iterrows():
    date = row['trade_date']
    close = row['close']
    pct = row['pct_chg']
    vol = row['vol'] / 10000
    turnover = row.get('turnover_rate', 0) or 0
    
    # 标记关键信号
    mark = ''
    if pct >= 9.5:
        mark = '***涨停'
    elif pct <= -9.5:
        mark = '***跌停'
    elif pct >= 5:
        mark = '**大涨'
    elif pct <= -5:
        mark = '**大跌'
    
    print(f'{date} | {close:>6.2f} | {pct:>+6.1f}% | {turnover:>5.1f}% | {vol:>6.0f}万手 {mark}')

print('\n分析要点：')
print(f'- 最高点：{daily["close"].max():.2f}（{daily.loc[daily["close"].idxmax(), "trade_date"]}）')
print(f'- 最低点：{daily["close"].min():.2f}（{daily.loc[daily["close"].idxmin(), "trade_date"]}）')
print(f'- 最大涨幅：{daily["pct_chg"].max():.1f}%')
print(f'- 最大跌幅：{daily["pct_chg"].min():.1f}%')
