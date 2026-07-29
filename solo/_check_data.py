# -*- coding: utf-8 -*-
import os, sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, 'd:/mystock/solo')
sys.path.insert(0, 'd:/mystock/solo/multi_factor_picker')
from dotenv import load_dotenv
load_dotenv('d:/mystock/config/.env')
import tushare as ts
ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

print('=== 检查20260729数据 ===')
for code in ['301165.SZ', '002440.SZ', '000938.SZ']:
    df = pro.daily(ts_code=code, start_date='20260729', end_date='20260729')
    if df is not None and len(df) > 0:
        r = df.iloc[0]
        print(f'{code}: 有数据 收盘={r["close"]} 涨幅={r["pct_chg"]}%')
    else:
        print(f'{code}: 无20260729数据')

print()
print('=== 检查数据最新日期 ===')
df = pro.daily(ts_code='000001.SZ', start_date='20260728', end_date='20260729')
if df is not None and len(df) > 0:
    dates = sorted(df['trade_date'].unique())
    print(f'000001.SZ 可用日期: {dates}')

print()
print('=== 检查交易日历 ===')
cal = pro.trade_cal(start_date='20260728', end_date='20260729')
if cal is not None:
    for _, r in cal.iterrows():
        print(f'  {r["cal_date"]} is_open={r["is_open"]}')
