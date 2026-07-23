# -*- coding: utf-8 -*-
import tushare as ts
ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

codes = {
    '000001.SH': '上证指数',
    '399001.SZ': '深证成指',
    '399006.SZ': '创业板指',
    '000300.SH': '沪深300',
}
print('=== 指数日线（收盘）===')
pro = ts.pro_api()
for code, name in codes.items():
    df = pro.index_daily(ts_code=code, start_date='20260723', end_date='20260723', limit=1)
    if df is not None and not df.empty:
        r = df.iloc[0]
        print(f'{name}: {r["close"]:.2f}  {r["pct_chg"]:+.2f}%  量:{r["vol"]/10000:.0f}万手')

# 持仓ETF
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

# 涨停
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
