# -*- coding: utf-8 -*-
import tushare as ts
import pandas as pd
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

# 六大指数
indices = [
    ('上证', '000001.SH'),
    ('深成', '399001.SZ'),
    ('创业板', '399006.SZ'),
    ('沪深300', '000300.SH'),
    ('中证500', '000905.SH'),
    ('中证1000', '000852.SH'),
]
print('=== 今日收盘 ===')
for name, code in indices:
    df = pro.daily(ts_code=code, start_date='20260722', end_date='20260722')
    if df is not None and not df.empty:
        r = df.iloc[0]
        print(f'{name}: {r["close"]}  {r["pct_chg"]:+.2f}%  量:{r["vol"]/10000:.0f}万手')

# 涨跌停
print()
print('=== 涨跌停 ===')
df_lim = pro.limit_list_d(trade_date='20260722', limit_type='U', ts_code='', adjust='')
df_lim_d = pro.limit_list_d(trade_date='20260722', limit_type='D', ts_code='', adjust='')
print(f'涨停: {len(df_lim)}家  跌停: {len(df_lim_d)}家')

# 涨停前15
print()
print('=== 涨停明细TOP15 ===')
if df_lim is not None and not df_lim.empty:
    top_lim = df_lim.sort_values('pct_chg', ascending=False).head(15)
    for _, r in top_lim.iterrows():
        print(f'  {r["name"]} {r["pct_chg"]:+.2f}%')

# 申万行业
print()
print('=== 申万行业涨跌TOP10 ===')
try:
    sw = pro.index_dailybasic(ts_code='850551.SI', start_date='20260722', end_date='20260722')
    print('(无申万行业数据)')
except:
    pass

# 尝试概念板块
print()
print('=== 今日板块涨跌 ===')
try:
    # 东财概念板块日涨跌
    sec = pro.ths_index_daily(ts_code='884001.TI', start_date='20260722', end_date='20260722')
    if sec is not None and not sec.empty:
        print(sec[['trade_date','close','pct_chg']].to_string())
except Exception as e:
    print(f'概念数据: {e}')

# 用指数成分获取板块信息
print()
print('=== 宽基指数日涨跌 ===')
big = ['000985.SH', '399005.SZ', '399008.SZ']
for code in big:
    df = pro.daily(ts_code=code, start_date='20260722', end_date='20260722')
    if df is not None and not df.empty:
        r = df.iloc[0]
        print(f'{code}: {r["close"]}  {r["pct_chg"]:+.2f}%')
