"""
检查今日(20260630)是否是交易日，以及Tushare数据是否已更新
"""
import tushare as ts
import os
from datetime import datetime

if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
pro = ts.pro_api()

today = '20260630'

print('=' * 60)
print(f'检查今日 {today} 是否是交易日')
print('=' * 60)
print()

# 1. 检查交易日历
print('1. 交易日历检查:')
cal = pro.trade_cal(exchange='SSE', start_date=today, end_date=today)
if cal is not None and len(cal) > 0:
    is_open = cal.iloc[0]['is_open']
    if is_open == 1:
        print(f'  ✅ 今日 {today} 是交易日 (is_open=1)')
    else:
        print(f'  ❌ 今日 {today} 非交易日 (is_open={is_open})')
else:
    print('  ❌ 无法获取交易日历')
print()

# 2. 检查Tushare是否有今日数据
print('2. Tushare数据更新检查:')
df = pro.daily(trade_date=today, fields='ts_code,trade_date,close')
if df is not None and len(df) > 0:
    print(f'  ✅ Tushare已有今日数据 ({len(df)}只股票)')
    print(f'  示例: {df.iloc[0]["ts_code"]} 收盘价={df.iloc[0]["close"]}')
else:
    print(f'  ❌ Tushare尚未更新今日数据')
    print('  说明: Tushare数据通常在收盘后17:00-18:00更新')
print()

# 3. 建议操作
print('3. 建议操作:')
if is_open == 1 and (df is None or len(df) == 0):
    print('  📊 今日是交易日，但数据尚未更新')
    print('  请等待17:00-18:00后数据更新，再运行趋势信号检测脚本')
elif is_open != 1:
    print('  📅 今日非交易日，无需运行脚本')
else:
    print('  ✅ 数据已更新，可以运行趋势信号检测脚本')
print()

print('=' * 60)
