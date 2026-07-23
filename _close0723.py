# -*- coding: utf-8 -*-
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

indices = [
    ('上证指数', '000001.SH'),
    ('深证成指', '399001.SZ'),
    ('创业板指', '399006.SZ'),
    ('沪深300', '000300.SH'),
    ('中证500', '000905.SH'),
    ('中证1000', '000852.SH'),
]

print('=== 今日收盘 ===')
for name, code in indices:
    df = pro.daily(ts_code=code, start_date='20260723', end_date='20260723')
    if df is not None and not df.empty:
        r = df.iloc[0]
        print(f'{name}: {r["close"]:.2f}  {r["pct_chg"]:+.2f}%  量:{r["vol"]/10000:.0f}万手')
    else:
        print(f'{name}: 无数据')

print()
print('=== 涨跌停 ===')
try:
    df_u = pro.limit_list_d(trade_date='20260723', limit_type='U', ts_code='', adjust='')
    df_d = pro.limit_list_d(trade_date='20260723', limit_type='D', ts_code='', adjust='')
    print(f'涨停: {len(df_u)}家  跌停: {len(df_d)}家')
    if df_u is not None and not df_u.empty:
        print('涨停TOP10:')
        for _, r in df_u.sort_values('pct_chg', ascending=False).head(10).iterrows():
            print(f'  {r["name"]} {r["pct_chg"]:+.2f}%')
except Exception as e:
    print(f'涨跌停: {e}')

print()
print('=== 英伟达今晚盘后财报 ===')
print('今晚关注：英伟达H100/H200产能、Blackwell放量节奏、中国出口管制影响、下一代架构路线图')
