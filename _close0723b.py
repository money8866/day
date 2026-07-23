# -*- coding: utf-8 -*-
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

# 六大指数实时
indices = {
    '000001': '上证指数',
    '399001': '深证成指',
    '399006': '创业板指',
    '000300': '沪深300',
    '000905': '中证500',
    '000852': '中证1000',
}
print('=== 实时行情 ===')
for code, name in indices.items():
    df = ts.get_realtime_quotes(code)
    if df is not None and not df.empty:
        price = float(df.iloc[0]['price'])
        pre = float(df.iloc[0]['pre_close'])
        pct = (price - pre) / pre * 100
        vol = float(df.iloc[0]['volume']) / 10000
        high = df.iloc[0]['high']
        low = df.iloc[0]['low']
        print(f'{name}: {price:.2f}  {pct:+.2f}%  量:{vol:.0f}万手  高:{high} 低:{low}')

# 中证2000 ETF
print()
print('=== 中证2000 ETF ===')
etfs = ['563300', '159531']
for code in etfs:
    df = ts.get_realtime_quotes(code)
    if df is not None and not df.empty:
        price = float(df.iloc[0]['price'])
        pre = float(df.iloc[0]['pre_close'])
        pct = (price - pre) / pre * 100
        print(f'{df.iloc[0]["name"]}: {price:.3f}  {pct:+.2f}%')

# 获取持仓ETF
print()
print('=== 持仓ETF ===')
positions = ['159516', '159611', '512480', '512760', '159865', '515050']
for code in positions:
    df = ts.get_realtime_quotes(code)
    if df is not None and not df.empty:
        price = float(df.iloc[0]['price'])
        pre = float(df.iloc[0]['pre_close'])
        pct = (price - pre) / pre * 100
        print(f'{df.iloc[0]["name"]}({code}): {price:.3f}  {pct:+.2f}%')
