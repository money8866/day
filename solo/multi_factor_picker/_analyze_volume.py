"""雅克科技量能分析 - 涨停日量比问题"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

daily = fetcher.pro.daily(ts_code='002409.SZ', start_date='20260601', end_date='20260610')
daily = daily.sort_values('trade_date')

print('=== 雅克科技6月1-10日量能 ===')
for _, row in daily.iterrows():
    date = row['trade_date']
    vol = row['vol'] / 10000  # 万手
    close = row['close']
    pct = row['pct_chg']
    mark = '***' if abs(pct) > 7 else ''
    print(f'{date} | {close:.2f} | {pct:+5.1f}% | {vol:6.0f}万手 {mark}')

print('\n量比计算：')
vol_today = daily[daily['trade_date']==20260610]['vol'].values[0] / 10000
vol_5day_avg = daily[daily['trade_date']<20260610].tail(5)['vol'].mean() / 10000
vol_ratio = vol_today / vol_5day_avg

print(f'6月10日成交量: {vol_today:.0f}万手')
print(f'前5日平均量: {vol_5day_avg:.0f}万手')
print(f'量比: {vol_ratio:.2f}')

print('\n分析：')
if vol_ratio >= 2.0:
    print('✓ 放量2倍以上，符合强势启动标准')
elif vol_ratio >= 1.5:
    print('⚠ 放量1.5-2倍，中等强度')
    print('建议：调整F8阈值，1.5倍得1分，2倍得2分')
else:
    print('✗ 量能不足1.5倍')

print('\n涨停日特殊性：')
print('- 涨停日量比偏低是正常的（封板后不再交易）')
print('- 不应要求涨停日量比>2')
print('- 应改用"换手率"或"成交额"判断')
