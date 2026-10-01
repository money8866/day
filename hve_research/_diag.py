# -*- coding: utf-8 -*-
import sqlite3, numpy as np, pandas as pd
DB=r'D:/mystock/cache_daily/stock_data.db'
c=sqlite3.connect(DB)
# 取一只银行股 + 一只高分红股 2023 全年，检查 pct_chg vs adj 收益
for code in ['600036.SH','000651.SZ','600519.SH']:
    q="""select d.trade_date,d.open,d.high,d.low,d.close,d.pre_close,d.pct_chg,d.vol,a.adj_factor
         from daily_cache d left join adj_factor_cache a on a.ts_code=d.ts_code and a.trade_date=d.trade_date
         where d.ts_code=? and d.trade_date between '20230301' and '20230831' order by d.trade_date"""
    df=pd.read_sql(q,c,params=(code,))
    df=df.dropna(subset=['adj_factor'])
    if len(df)<50: print(code,'no adj',len(df)); continue
    adj_close=df['close']*df['adj_factor']
    ret_adj=adj_close.pct_change()
    ret_raw=df['close']/df['pre_close']-1
    ret_pct=df['pct_chg']/100
    d_raw=(ret_pct-ret_raw).abs().max()
    d_adj=(ret_pct-ret_adj).abs().max()
    print(f"{code} n={len(df)} maxdiff(pct vs raw)={d_raw:.5f} maxdiff(pct vs adj)={d_adj:.5f}")
    # 找差异最大的几天
    df['diff_raw']=(ret_pct-ret_raw).abs()
    top=df.nlargest(3,'diff_raw')[['trade_date','close','pre_close','pct_chg','adj_factor']]
    print(top.to_string(index=False))
