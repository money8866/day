# -*- coding: utf-8 -*-
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

# 六大指数今日收盘
codes = ['000001.SH','399001.SZ','399006.SZ','000300.SH','000905.SH','000852.SH']
names = ['上证指数','深证成指','创业板指','沪深300','中证500','中证1000']

print('=== 六大指数今日收盘 ===')
for name, code in zip(names, codes):
    df = ts.get_realtime_quotes(code.split('.')[0])
    if df is not None and not df.empty:
        price = float(df.iloc[0]['price'])
        pre = float(df.iloc[0]['pre_close'])
        pct = (price - pre) / pre * 100
        print(f'{name}: {price:.2f}  {pct:+.2f}%  量:{float(df.iloc[0]['volume'])/10000:.0f}万手')

# 用Tushare获取指数收盘
print()
print('=== Tushare指数日线 ===')
pro = ts.pro_api()
for name, code in zip(names, codes):
    try:
        df = pro.index_daily(ts_code=code, start_date='20260722', end_date='20260722', limit=1)
        if df is not None and not df.empty:
            r = df.iloc[0]
            print(f'{name}: {r["close"]}  {r["pct_chg"]:+.2f}%  量:{r["vol"]/10000:.0f}万手')
    except Exception as e:
        print(f'{name}: error - {e}')
