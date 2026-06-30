# -*- coding: utf-8 -*-
import tushare as ts
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

# 检查最近交易日
cal = pro.trade_cal(exchange='SSE', start_date='20260620', end_date='20260626')
cal = cal[cal['is_open']==1].sort_values('cal_date', ascending=False)
print(f'最近交易日(日历): {cal["cal_date"].iloc[0]}')

# 检查stk_factor_pro最新数据
df = pro.stk_factor_pro(ts_code='600000.SH', start_date='20260620', end_date='20260626')
if df is not None and len(df) > 0:
    print(f'stk_factor_pro最新: {df["trade_date"].max()}')
else:
    print('stk_factor_pro无数据')

# 检查daily最新数据
df2 = pro.daily(ts_code='600000.SH', start_date='20260620', end_date='20260626')
if df2 is not None and len(df2) > 0:
    print(f'daily最新: {df2["trade_date"].max()}')
else:
    print('daily无数据')
