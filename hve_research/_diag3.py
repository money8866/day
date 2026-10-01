# -*- coding: utf-8 -*-
import sqlite3, pandas as pd, numpy as np
DB=r'D:/mystock/cache_daily/stock_data.db'
c=sqlite3.connect(DB)
y=pd.read_sql("select substr(trade_date,1,4) yr,count(distinct ts_code) n from daily_cache group by yr order by yr",c)
print(y.to_string(index=False))
# 2021 有、2026 无 → 疑似退市
a=set(x[0] for x in c.execute("select distinct ts_code from daily_cache where trade_date like '2021%'"))
b=set(x[0] for x in c.execute("select distinct ts_code from daily_cache where trade_date like '2026%'"))
print('2021 codes',len(a),'2026 codes',len(b),'delisted-like',len(a-b))
print('sample delisted',list(a-b)[:10])
# 交易日历
cal=pd.read_sql("select distinct trade_date from index_daily_cache where ts_code='000001.SH' order by trade_date",c)
print('calendar',len(cal),cal['trade_date'].iloc[0],cal['trade_date'].iloc[-1])
# 停牌检测：某股票在某交易日缺失
import itertools
sb=pd.read_csv(r'D:/mystock/cache_daily/stock_basic.csv')
print('basic',len(sb), sb.columns.tolist())
print(sb['ts_code'].str[-2:].value_counts().to_dict())
def mkt(code):
    if code.endswith('.BJ'): return 'BJ'
    p=code[:3]
    if code.endswith('.SH'):
        return 'STAR' if p.startswith('68') else 'MAIN'
    else:
        return 'STAR' if p.startswith('30') else 'MAIN'
sb['mkt']=sb['ts_code'].map(mkt)
print(sb['mkt'].value_counts().to_dict())
print('ST names', sb['name'].str.contains('ST').sum())
